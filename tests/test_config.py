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
