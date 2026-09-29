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


def _launcher_args(project_root: Path, tmp_path: Path, *args: str) -> list[str]:
    """Run bin/mpv-uos with a fake mpv that prints its argv (one per line)."""
    fake = tmp_path / "fake-mpv"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    fake.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "MPV_UOS_MPV": str(fake), "MPV_UOS_RUNTIME_DIR": str(tmp_path)}
    out = subprocess.run([str(project_root / "bin" / "mpv-uos"), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.splitlines()


def test_launcher_without_files_opens_the_window(project_root: Path, tmp_path: Path):
    mode = "--player-operation-mode=pseudo-gui"
    assert mode in _launcher_args(project_root, tmp_path)               # plain `mpv-uos` shows the home screen
    assert mode in _launcher_args(project_root, tmp_path, "--fs")       # options only, still no file
    assert mode not in _launcher_args(project_root, tmp_path, "video.mkv")
    assert mode not in _launcher_args(project_root, tmp_path, "--", "-raro.mkv")
    assert mode not in _launcher_args(project_root, tmp_path, "--idle=yes")  # tests and scripts choose their own mode
