"""bin/mpv-uos launches the system mpv with the project config dir and an IPC socket."""

import subprocess
from pathlib import Path


def test_launcher_runs_system_mpv(project_root: Path):
    out = subprocess.run([str(project_root / "bin" / "mpv-uos"), "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.startswith("mpv v0."), out.stdout[:80]


def test_launcher_uses_project_config_dir_and_socket(mpv_headless, project_root: Path):
    assert Path(mpv_headless.get("config-dir")) == project_root / "mpv-config"
    assert Path(mpv_headless.get("input-ipc-server")) == mpv_headless.socket
    assert mpv_headless.get("options/osc") is False  # mpv.conf was loaded (osc=no)
    assert mpv_headless.get("options/osd-bar") is False
