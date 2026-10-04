"""H28 · the AppImage (tools/build_appimage.sh): it starts headless with the system mpv, mu-core starts mpvd with the
bundled Python (not the checkout's .venv), caches and the writable yt-dlp copy go to the user's folders, and nothing
is written inside the (read-only) image. Built on demand with MU_BUILD_APPIMAGE=1, otherwise uses dist/ if present."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import APP_FOLDER, start_mpv

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / "dist" / f"{APP_FOLDER}-x86_64.AppImage"


@pytest.fixture(scope="module")
def image() -> Path:
    if os.environ.get("MU_BUILD_APPIMAGE") == "1":
        subprocess.run([str(ROOT / "tools" / "build_appimage.sh")], check=True, capture_output=True, timeout=900)
    if not IMAGE.is_file():
        pytest.skip(f"sin dist/{IMAGE.name} (tools/build_appimage.sh o MU_BUILD_APPIMAGE=1)")
    return IMAGE


def test_appimage_contents(image, tmp_path):
    out = subprocess.run([str(image), "--appimage-extract"], cwd=tmp_path, capture_output=True, text=True,
                         env={**os.environ, "APPIMAGE_EXTRACT_AND_RUN": ""}, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    app = tmp_path / "squashfs-root"
    assert (app / "AppRun").is_file() and (app / ".DirIcon").exists()
    assert (app / "usr/share/mpv-uos/bin/mpv-uos").is_file()
    assert (app / "usr/share/mpv-uos/mpv-config/scripts/uosc/main.lua").is_file()
    # H49 · los catálogos TIENEN que viajar: sin ellos todo cae al castellano y nadie se entera, porque ese
    # respaldo silencioso es justo lo que diseñamos. La primera versión del paquete se los dejó fuera.
    for lang in ("en", "fr"):
        cat = app / "usr/share/mpv-uos/locales" / f"{lang}.json"
        assert cat.is_file(), f"falta locales/{lang}.json en el paquete"
        assert len(json.loads(cat.read_text(encoding="utf-8"))) > 1000, f"locales/{lang}.json llega cojo"
    py = app / "usr/share/mpv-uos/.venv/bin/python"
    assert py.is_symlink() and py.resolve() == (app / "usr/python/bin/python3.12").resolve()
    # the bundled interpreter runs from its new place and imports mpvd from the app
    res = subprocess.run([str(py), "-c", "import sys, mpvd, jeepney; print(sys.prefix); print(mpvd.__file__)"],
                         cwd=app / "usr/share/mpv-uos", capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    prefix, mod = res.stdout.split()
    assert Path(prefix).resolve() == (app / "usr/python").resolve() and "squashfs-root" in mod
    assert not (app / "usr/share/mpv-uos/.cache").exists() and not (app / "usr/share/mpv-uos/tests").exists()


def test_appimage_runs_headless(image, tmp_path):
    home = tmp_path / "home"
    env = {"APPIMAGE_EXTRACT_AND_RUN": "1", "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
           "XDG_DATA_HOME": str(home / ".local/share"), "MPV_UOS_CACHE_DIR": "", "MPV_UOS_VENDOR_BIN": "",
           "TMPDIR": str(tmp_path / "t")}
    (tmp_path / "t").mkdir()
    for k in ("MPV_UOS_CACHE_DIR", "MPV_UOS_VENDOR_BIN"):
        env.pop(k)
    run = tmp_path / "run"
    h = start_mpv(run, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"],
                  env={**{k: v for k, v in os.environ.items() if not k.startswith("MPV_UOS_")}, **env}, launcher=image)
    try:
        core = h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=60)
        assert "appimage_extracted" in core.get("root", ""), core
        hook = h.wait_property("script-opts", lambda v: bool(v) and "ytdl_hook-ytdl_path" in v, timeout=20)
        # start_mpv gives every run its own MPV_UOS_DATA_DIR: the writable yt-dlp copy lives there
        ytdl = Path(hook["ytdl_hook-ytdl_path"].split(":")[0])
        assert ytdl.is_file() and ytdl.parent.name == "bin" and run in ytdl.parents
        assert (home / ".cache/mpv-uos").is_dir()
    finally:
        h.stop()
