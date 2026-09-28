"""docs/ATAJOS.md must document every key bound in mpv-config/input.conf (and stay in sync with the scripts)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def input_conf_bindings() -> list[tuple[str, str]]:
    rows = []
    for line in (ROOT / "mpv-config" / "input.conf").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, rest = line.split(None, 1)
        rows.append((key, rest.split("#")[0].strip()))
    return rows


def test_every_binding_is_documented():
    doc = (ROOT / "docs" / "ATAJOS.md").read_text(encoding="utf-8")
    missing = [key for key, _ in input_conf_bindings() if f"`{key}`" not in doc]
    assert not missing, f"teclas sin documentar en docs/ATAJOS.md: {missing}"
    # every script-binding referenced in the doc exists in a script (mu_*) or is a uosc/mpv one
    lua = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "mpv-config" / "scripts").glob("mu-*/main.lua"))
    lua += (ROOT / "mpv-config" / "scripts" / "mu-core.lua").read_text(encoding="utf-8")
    for key, cmd in input_conf_bindings():
        m = re.match(r"script-binding (mu_\w+)/([\w-]+)", cmd)
        if m:
            assert f"'{m.group(2)}'" in lua, f"{key}: {cmd} no existe en los scripts"
