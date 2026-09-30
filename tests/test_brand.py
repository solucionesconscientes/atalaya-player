"""H33: one place for the app's identity (brand.json) and the approved logo everywhere (desktop, PWA, tray)."""

from __future__ import annotations

import json
import re
import struct
import subprocess

from mpvd import brand
from tests.conftest import ROOT

WWW = ROOT / "mpvd" / "remote" / "www"
LOGO = ROOT / "docs" / "marca" / "logo-anillo.svg"


def test_brand_json_is_the_single_source():
    data = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
    assert brand.app_name() == data["name"] and brand.app_id() == data["id"] and brand.folder_name() == data["folder"]
    assert brand.brand()["colors"]["amber"].lower() == "#ffb020"
    # Python users of the name read it from brand.py, never a literal
    from mpvd import mpris
    from mpvd.ytdl.downloads import default_media_dir
    assert mpris.APP_NAME == data["name"] and default_media_dir("video").name == data["folder"]
    # Lua: mu.brand is what mu-menu and mu.nav use for titles and breadcrumbs
    nav = (ROOT / "mpv-config" / "script-modules" / "mu" / "nav.lua").read_text(encoding="utf-8")
    assert "M.HOME = require('mu.brand').name" in nav
    menu = (ROOT / "mpv-config" / "scripts" / "mu-menu" / "main.lua").read_text(encoding="utf-8")
    assert "show('MPV-UOS'" not in menu and "show(brand.name" in menu


def test_brand_falls_back_when_the_file_is_broken(tmp_path, monkeypatch):
    (tmp_path / "mpv-config").mkdir()
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "brand.json").write_text("{roto", encoding="utf-8")
    monkeypatch.setenv("MPV_UOS_ROOT", str(tmp_path))
    brand.brand.cache_clear()
    try:
        assert brand.app_name() == "MPV-UOS"
        (tmp_path / "brand.json").write_text('{"name": "Sintonía", "colors": {"amber": "#000000"}}', encoding="utf-8")
        brand.brand.cache_clear()
        assert brand.app_name() == "Sintonía" and brand.app_id() == "mpv-uos"
        assert brand.brand()["colors"]["amber"] == "#000000" and brand.brand()["colors"]["signal"] == "#3D7BFF"
    finally:
        brand.brand.cache_clear()


def png_size(path):
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_logo_is_the_app_and_pwa_icon():
    assert (WWW / "icon.svg").read_text(encoding="utf-8") == LOGO.read_text(encoding="utf-8")
    assert png_size(WWW / "icon-192.png") == (192, 192) and png_size(WWW / "icon-512.png") == (512, 512)
    manifest = json.loads((WWW / "manifest.webmanifest").read_text(encoding="utf-8"))
    srcs = {i["src"] for i in manifest["icons"]}
    assert {"/icon.svg", "/icon-192.png", "/icon-512.png"} <= srcs
    assert manifest["theme_color"].lower() == "#0d1320"
    for name in ("logo-anillo.svg", "logo-sin-fondo.svg", "logo-mono.svg"):
        svg = (ROOT / "docs" / "marca" / name).read_text(encoding="utf-8")
        assert 'aria-label="MPV-UOS"' in svg and "Sintonía" not in svg   # the name is still pending
    assert "<rect" not in (ROOT / "docs" / "marca" / "logo-sin-fondo.svg").read_text(encoding="utf-8")
    # service worker and HTTP routes know the PNGs
    assert "/icon-192.png" in (WWW / "sw.js").read_text(encoding="utf-8")
    from mpvd.remote.service import STATIC
    assert STATIC["/icon-512.png"] == "icon-512.png"


def test_lua_brand_module_reads_brand_json_and_converts_colours(tmp_path):
    """mu.brand reads <MPV_UOS_ROOT>/brand.json: another name there shows up in Lua (the rename is one edit)."""
    import os
    (tmp_path / "brand.json").write_text('{"name": "Prueba", "id": "prueba", "colors": {"amber": "#112233"}}',
                                         encoding="utf-8")
    script = ROOT / "tests" / "fixtures" / "lua" / "brand_probe.lua"
    out = subprocess.run(["mpv", "--no-config", "--idle=once", "--vo=null", "--ao=null", f"--script={script}",
                          "--msg-level=all=no,brand_probe=info", f"--script-opts=brand_probe-root={ROOT}"],
                         capture_output=True, text=True, timeout=30, env={**os.environ, "MPV_UOS_ROOT": str(tmp_path)})
    m = re.search(r"BRAND (\S+) (\S+) (\S+)", out.stdout + out.stderr)
    assert m, out.stdout + out.stderr
    assert (m.group(1), m.group(2), m.group(3)) == ("Prueba", "prueba", "332211")
