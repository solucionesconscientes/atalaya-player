"""tools/install.sh: user install into a throwaway prefix under tmp/ (never the real ~/.local)."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.conftest import ROOT, TMP

INSTALL = ROOT / "tools" / "install.sh"


@pytest.fixture
def prefix():
    base = TMP / "test-install" / uuid.uuid4().hex[:8]
    (base / "bin").mkdir(parents=True)
    env = dict(os.environ, MPV_UOS_BIN_DIR=str(base / "bin"), XDG_DATA_HOME=str(base / "share"),
               PATH=f"{base / 'bin'}:{os.environ['PATH']}")
    env.pop("MPV_UOS_CACHE_DIR", None)
    env.pop("MPV_UOS_DATA_DIR", None)
    try:
        yield base, env
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(INSTALL), *args], env=env, capture_output=True, text=True, timeout=60, check=check)


def test_install_and_uninstall(prefix):
    base, env = prefix
    out = run(env, "--no-sync", "--no-vendor").stdout
    launcher = base / "bin" / "mpv-uos"
    desktop = base / "share" / "applications" / "mpv-uos.desktop"
    icon = base / "share" / "icons" / "hicolor" / "scalable" / "apps" / "mpv-uos.svg"
    assert launcher.is_file() and os.access(launcher, os.X_OK), out
    assert desktop.is_file() and icon.is_file()
    text = desktop.read_text(encoding="utf-8")
    assert f"Exec={launcher} --player-operation-mode=pseudo-gui" in text and "%U" in text
    assert "MimeType=" in text and "video/x-matroska;" in text and "Icon=mpv-uos" in text
    if shutil.which("desktop-file-validate"):
        assert subprocess.run(["desktop-file-validate", str(desktop)], capture_output=True, text=True).stdout == ""
    # the launcher really runs the checkout's bin/mpv-uos (and so the system mpv with the portable config dir)
    ver = subprocess.run([str(launcher), "--version"], env=env, capture_output=True, text=True, timeout=30)
    assert ver.returncode == 0 and ver.stdout.startswith("mpv")
    assert "MPV_UOS_CACHE_DIR" not in launcher.read_text(encoding="utf-8")
    # idempotent
    run(env, "--no-sync", "--no-vendor")
    # uninstall removes only our files
    other = base / "share" / "applications" / "otra.desktop"
    other.write_text("[Desktop Entry]\nType=Application\nName=Otra\nExec=true\n", encoding="utf-8")
    run(env, "--uninstall")
    assert not launcher.exists() and not desktop.exists() and not icon.exists() and other.exists()


def test_install_xdg_env_and_refuses_foreign_launcher(prefix):
    base, env = prefix
    fake = base / "fake-mpv"
    fake.write_text('#!/bin/sh\necho "CACHE=$MPV_UOS_CACHE_DIR"\necho "DATA=$MPV_UOS_DATA_DIR"\necho "ARGS=$*"\n', encoding="utf-8")
    fake.chmod(0o755)
    run(env, "--no-sync", "--no-vendor", "--xdg")
    env2 = dict(env, MPV_UOS_MPV=str(fake), XDG_CACHE_HOME=str(base / "cache"), XDG_RUNTIME_DIR=str(base / "rt"))
    out = subprocess.run([str(base / "bin" / "mpv-uos"), "a.mkv"], env=env2, capture_output=True, text=True, timeout=30).stdout
    assert f"CACHE={base / 'cache' / 'mpv-uos'}" in out and f"DATA={base / 'share' / 'mpv-uos'}" in out
    assert f"--config-dir={ROOT / 'mpv-config'}" in out and out.rstrip().endswith("a.mkv")
    run(env, "--uninstall")
    # a launcher that is not ours is never overwritten nor removed
    foreign = base / "bin" / "mpv-uos"
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    res = run(env, "--no-sync", "--no-vendor", check=False)
    assert res.returncode == 1 and "no se sobrescribe" in res.stderr
    run(env, "--uninstall")
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_install_dry_run_writes_nothing(prefix):
    base, env = prefix
    out = run(env, "--dry-run", "--default").stdout
    assert "(dry-run) uv sync" in out and "(dry-run)" in out
    assert not (base / "bin" / "mpv-uos").exists() and not (base / "share").exists()
