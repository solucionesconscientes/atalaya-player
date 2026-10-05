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
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MPV_UOS_MPV": str(fake), "MPV_UOS_RUNTIME_DIR": str(tmp_path),
           "MPV_UOS_DATA_DIR": str(tmp_path / "data")}
    out = subprocess.run([str(project_root / "bin" / "mpv-uos"), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.splitlines()


def test_launcher_without_files_opens_the_window(project_root: Path, tmp_path: Path):
    mode = "--player-operation-mode=pseudo-gui"
    assert mode in _launcher_args(project_root, tmp_path)               # plain `mpv-uos` shows the home screen
    assert mode in _launcher_args(project_root, tmp_path, "--fs")       # options only, still no file
    assert mode not in _launcher_args(project_root, tmp_path, "video.mkv")
    assert mode not in _launcher_args(project_root, tmp_path, "--", "-raro.mkv")
    assert mode not in _launcher_args(project_root, tmp_path, "--idle=yes")  # tests and scripts choose their own mode


def test_launcher_always_idles_and_never_inherits_the_socket(project_root: Path, tmp_path: Path):
    """A failed load must not close the player, and each instance gets its own socket even if the environment
    carries another instance's (e.g. a file manager spawned by mpv)."""
    import os

    args = _launcher_args(project_root, tmp_path, "video.mkv")
    assert "--idle=yes" in args
    assert "--idle=yes" not in _launcher_args(project_root, tmp_path, "--idle=once", "video.mkv")
    fake = tmp_path / "fake-mpv"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MPV_UOS_MPV": str(fake), "MPV_UOS_RUNTIME_DIR": str(tmp_path),
           "MPV_UOS_DATA_DIR": str(tmp_path / "data"), "MPV_UOS_SOCKET": str(tmp_path / "mpv-1.sock")}
    out = subprocess.run([str(project_root / "bin" / "mpv-uos"), "x.mkv"], capture_output=True, text=True, check=True,
                         env=env).stdout.splitlines()
    ipc = next(a for a in out if a.startswith("--input-ipc-server="))
    assert ipc != f"--input-ipc-server={tmp_path / 'mpv-1.sock'}"
    assert f"--watch-later-dir={tmp_path / 'data' / 'watch_later'}" in out
    # dead sockets are cleaned, live ones (our own pid) are kept
    dead = tmp_path / "mpv-999999.sock"
    dead.touch()
    live = tmp_path / f"mpv-{os.getpid()}.sock"
    live.touch()
    _launcher_args(project_root, tmp_path, "x.mkv")
    assert not dead.exists() and live.exists()


def test_launcher_migrates_data_only_for_the_default_location(project_root: Path, tmp_path: Path):
    fake = tmp_path / "fake-mpv"
    _launcher_args(project_root, tmp_path)  # creates the fake binary
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "MPV_UOS_MPV": str(fake),
           "MPV_UOS_RUNTIME_DIR": str(tmp_path), "MPV_UOS_DATA_DIR": str(tmp_path / "explicit")}
    subprocess.run([str(project_root / "bin" / "mpv-uos"), "x.mkv"], check=True, env=env, capture_output=True)
    explicit = tmp_path / "explicit"
    assert (explicit / "watch_later").is_dir() and not (explicit / "iptv.sqlite3").exists()


def test_launcher_opens_mpv_uos_links_at_their_minute(project_root: Path, tmp_path: Path):
    """«Mis notas» links (H17): the desktop entry passes them after `--`; they become per-file groups before it."""
    from mpvd.notes import link

    target = "/home/ser/Vídeos/Mi peli: parte 1 (2024) & más.mkv"
    args = _launcher_args(project_root, tmp_path, "--player-operation-mode=pseudo-gui", "--",
                          link(target, 83.4), "/otro.mkv", link("https://example.com/v?a=1&b=2"))
    i = args.index("--{")
    assert args[i:i + 5] == ["--{", "--start=83.4", target, "--}", "https://example.com/v?a=1&b=2"]
    assert args.index("--") == i + 5 and args[-1] == "/otro.mkv"
    assert not any(a.startswith("mpv-uos://") for a in args)
    # a malformed time is dropped, the file still opens
    args = _launcher_args(project_root, tmp_path, "mpv-uos://open?path=%2Fx.mkv&t=1%3Bquit")
    assert args[-1] == "/x.mkv" and not any(a.startswith("--start") for a in args)


def test_el_nombre_del_socket_de_las_pruebas_no_se_puede_leer_como_un_pid(tmp_path):
    """H63 · bin/mpv-uos borra `mpv-<pid>.sock` cuando ese PID ya no existe, y decide leyendo el NOMBRE. Un tag de 8
    hexadecimales sale todo numérico el 2,3 % de las veces, y entonces la segunda instancia le borraba el socket a la
    primera, viva: así fallaba test_two_instances_merge 1 de cada 23 veces. El nombre tiene que llevar una letra."""
    from tests.conftest import start_mpv
    h = start_mpv(tmp_path / "run")
    try:
        nombre = h.socket.stem.removeprefix("mpv-")
        assert not nombre.isdigit(), f"«{nombre}» se puede leer como un PID: el lanzador lo borraría"
        assert h.socket.exists()
    finally:
        h.stop()
