"""mpv.conf, input.conf and vendor.lock stay consistent with the installed mpv and the vendored files."""

import re
from pathlib import Path


def _conf_keys(path: Path) -> list[str]:
    keys = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


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
