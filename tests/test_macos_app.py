"""H28 · tools/build_macos_app.sh builds a well-formed .app on any system: Info.plist parses and declares the
executable, icon, mpv-uos:// scheme and document types; the launcher is valid Bash and points at this checkout."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_macos_bundle(tmp_path):
    out = subprocess.run([str(ROOT / "tools" / "build_macos_app.sh"), "--out", str(tmp_path)], capture_output=True,
                         text=True, timeout=60, check=True).stdout.strip()
    app = Path(out)
    assert app.name.endswith(".app") and app.parent == tmp_path
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    exe = app / "Contents/MacOS" / info["CFBundleExecutable"]
    assert exe.is_file() and exe.stat().st_mode & 0o111
    assert (app / "Contents/Resources" / info["CFBundleIconFile"]).is_file()
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["mpv-uos"]
    assert info["CFBundlePackageType"] == "APPL" and (app / "Contents/PkgInfo").read_text() == "APPL????"
    script = exe.read_text()
    assert f'ROOT="{ROOT}"' in script and '"$ROOT/bin/mpv-uos"' in script and "/opt/homebrew/bin" in script
    subprocess.run(["bash", "-n", str(exe)], check=True)
