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
    (base / "config").mkdir()
    # XDG_CONFIG_HOME too: --default/--uninstall edit mimeapps.list, never the user's real one
    env = dict(os.environ, MPV_UOS_BIN_DIR=str(base / "bin"), XDG_DATA_HOME=str(base / "share"),
               XDG_CONFIG_HOME=str(base / "config"), XDG_CURRENT_DESKTOP="",
               MPV_UOS_DATA_DIR=str(base / "data"), PATH=f"{base / 'bin'}:{os.environ['PATH']}")
    env.pop("MPV_UOS_CACHE_DIR", None)
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
    env2.pop("MPV_UOS_DATA_DIR")  # the default location is what is being checked here
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


def test_moved_checkout_is_reported(prefix):
    base, env = prefix
    run(env, "--no-sync", "--no-vendor")
    launcher = base / "bin" / "mpv-uos"
    launcher.write_text(launcher.read_text(encoding="utf-8").replace(str(ROOT), str(base / "movida")), encoding="utf-8")
    out = subprocess.run([str(launcher), "--version"], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 1 and "has movido la carpeta" in out.stderr


def test_mime_types_include_the_system_mpv_ones(prefix):
    base, env = prefix
    fake = base / "mpv.desktop"
    fake.write_text("[Desktop Entry]\nMimeType=audio/x-ape;audio/x-wavpack;video/mp4;\n", encoding="utf-8")
    run({**env, "MPV_UOS_SYSTEM_MPV_DESKTOP": str(fake)}, "--no-sync", "--no-vendor")
    mime = next(line for line in (base / "share" / "applications" / "mpv-uos.desktop").read_text(encoding="utf-8")
                .splitlines() if line.startswith("MimeType="))
    types = mime.removeprefix("MimeType=").split(";")
    assert "audio/x-ape" in types and "audio/x-wavpack" in types and types.count("video/mp4") == 1


@pytest.mark.skipif(not shutil.which("xdg-mime"), reason="xdg-mime not installed")
def test_default_player_is_restored_on_uninstall(prefix):
    base, env = prefix
    fake = base / "mpv.desktop"
    fake.write_text("[Desktop Entry]\nMimeType=video/mp4;\n", encoding="utf-8")
    env = {**env, "MPV_UOS_SYSTEM_MPV_DESKTOP": str(fake)}
    mimeapps = base / "config" / "mimeapps.list"
    mimeapps.write_text("[Default Applications]\nvideo/mp4=vlc.desktop\n", encoding="utf-8")
    run(env, "--no-sync", "--no-vendor", "--default")
    assert "video/mp4=mpv-uos.desktop" in mimeapps.read_text(encoding="utf-8")
    run(env, "--uninstall")
    text = mimeapps.read_text(encoding="utf-8")
    assert "video/mp4=vlc.desktop" in text and "mpv-uos.desktop" not in text
