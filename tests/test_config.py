"""mpv.conf, input.conf and vendor.lock stay consistent with the installed mpv and the vendored files."""

import re
from pathlib import Path


PROFILE_KEYS = {"profile-desc", "profile-cond", "profile-restore"}  # valid only inside a [profile] section


def _conf_keys(path: Path) -> list[str]:
    keys = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or (line.startswith("[") and line.endswith("]")):
            continue
        key = line.split("=", 1)[0].strip()
        if key not in PROFILE_KEYS:
            keys.append(key)
    return keys


def test_deinterlace_profile_switches_to_copy_decoding(mpv_headless, media_dir):
    """With hwdec=vaapi mpv would deinterlace with vavpp, missing on Intel Skylake's iHD driver: deinterlacing must
    switch decoding to copy (bwdif) and switch back afterwards."""
    import asyncio

    async def go(c):
        await c.set_property("hwdec", "vaapi,auto-safe")
        await c.command("loadfile", str(media_dir / "video30.mkv"))
        await c.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.2, timeout=20)
        await c.set_property("deinterlace", "yes")
        await asyncio.sleep(0.8)
        during = await c.get_property("hwdec")
        await c.set_property("deinterlace", "no")
        await asyncio.sleep(0.8)
        return during, await c.get_property("hwdec")

    during, after = mpv_headless.run(go)
    assert during[0] == "vaapi-copy"
    assert after[0] == "vaapi"


def test_mpv_conf_options_exist_in_installed_mpv(project_root: Path, mpv_option_names: set[str]):
    keys = _conf_keys(project_root / "mpv-config" / "mpv.conf")
    assert keys, "mpv.conf has no options"
    unknown = [k for k in keys if k not in mpv_option_names and k.removeprefix("no-") not in mpv_option_names]
    assert not unknown, f"options unknown to this mpv: {unknown}"


def test_la_ventana_abre_maximizada(mpv_headless):
    """Pedido por Ser (2026-10-15): «abrir siempre maximizado, no restaurado» (ADR-132). Con --vo=null no hay
    ventana, pero mpv refleja igual la opción en la propiedad, así que se exige sin abrir nada."""
    assert mpv_headless.get("window-maximized") is True


def test_input_conf_parses_without_errors(mpv_headless, project_root: Path):
    log = mpv_headless.log_text()
    m = re.search(r"input\.conf parsed: (\d+) binds", log)
    assert m, "mpv did not report parsing input.conf"
    expected = sum(1 for line in (project_root / "mpv-config" / "input.conf").read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#"))
    assert int(m.group(1)) == expected
    assert not [ln for ln in log.splitlines() if "][e][input]" in ln or "][w][input]" in ln]


def test_vendor_lock_matches_installed_uosc(project_root: Path):
    lock = dict(
        line.split("=", 1)
        for line in (project_root / "vendor.lock").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    main_lua = (project_root / "mpv-config" / "scripts" / "uosc" / "main.lua").read_text(encoding="utf-8")
    assert f"uosc_version = '{lock['UOSC_VERSION']}'" in main_lua
    assert (project_root / "mpv-config" / "scripts" / "thumbfast.lua").exists()
    assert (project_root / "mpv-config" / "fonts" / "uosc_icons.otf").exists()
    assert (project_root / "mpv-config" / "script-opts" / "uosc.conf").exists()
    assert (project_root / "mpv-config" / "script-opts" / "thumbfast.conf").exists()


def test_el_parche_del_panel_sigue_aplicado(project_root: Path):
    """ADR-136 · el panel de canales necesita dos cosas que la API de uosc no da (ancho fijo y pegado a la
    izquierda). Van en patches/ y tools/vendor.sh las reaplica; este test avisa si al actualizar uosc se perdió."""
    menu = (project_root / "mpv-config" / "scripts" / "uosc" / "elements" / "Menu.lua").read_text(encoding="utf-8")
    for campo in ("panel_width", "panel_align", "follow_selection"):
        assert campo in menu, f"falta el parche de uosc ({campo})"
    assert (project_root / "patches" / "uosc-5.13.0-panel.patch").is_file()
    vendor = (project_root / "tools" / "vendor.sh").read_text(encoding="utf-8")
    assert "patches/*.patch" in vendor, "vendor.sh tiene que reaplicar los parches al actualizar uosc"


def test_win32_usa_el_bundle_de_ca_del_paquete(tmp_path, monkeypatch):
    """Windows: el Python del paquete no lee el almacén del sistema y sin raíces falla el DoH del enlace (F2).
    El bundle que viaja en vendor/cacert.pem se usa solo si nadie ha fijado SSL_CERT_FILE, y nunca fuera de Windows."""
    from mpvd.config import use_bundled_ca_bundle

    bundle = tmp_path / "cacert.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert use_bundled_ca_bundle(bundle, "win32") is True
    assert Path(__import__("os").environ["SSL_CERT_FILE"]) == bundle
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert use_bundled_ca_bundle(bundle, "linux") is False
    assert "SSL_CERT_FILE" not in __import__("os").environ
    monkeypatch.setenv("SSL_CERT_FILE", "/otro/cacert.pem")
    assert use_bundled_ca_bundle(bundle, "win32") is False
    assert Path(__import__("os").environ["SSL_CERT_FILE"]) == Path("/otro/cacert.pem")


def test_win32_sin_bundle_no_toca_el_entorno(tmp_path, monkeypatch):
    from mpvd.config import use_bundled_ca_bundle

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert use_bundled_ca_bundle(tmp_path / "no-existe.pem", "win32") is False
    assert "SSL_CERT_FILE" not in __import__("os").environ
