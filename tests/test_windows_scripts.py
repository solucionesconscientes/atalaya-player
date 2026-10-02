"""H28 · Windows scripts tested from Linux: bin/mpv-uos.ps1 (+ .cmd) and tools/install.ps1.

Without PowerShell only static checks run (the .ps1 launcher passes mpv the same key options as bin/mpv-uos, no
PowerShell-7-only syntax, the Windows pins in vendor.lock). With a PowerShell 7 (``$MU_PWSH``, ``pwsh`` on PATH or the
portable copy in ``<checkout>/.cache/pwsh``, fetched by the network test below) the scripts are parsed with the real
parser and run: the launcher's -DryRun must build the same mpv command line as bin/mpv-uos, the installer's
download/verify/unzip functions run against a local HTTP server, and install + uninstall run in a temporary folder
(no Start menu or registry off Windows). Nothing here proves behaviour on a real Windows (docs/PLATAFORMAS.md).
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.request
import zipfile
from pathlib import Path

import pytest
from tests.conftest import APP

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "bin" / "mpv-uos.ps1"
CMD = ROOT / "bin" / "mpv-uos.cmd"
INSTALL = ROOT / "tools" / "install.ps1"
BASH = ROOT / "bin" / "mpv-uos"
PWSH_DIR = ROOT / ".cache" / "pwsh"
PWSH_VERSION = "7.6.6"
PWSH_RELEASE = f"https://github.com/PowerShell/PowerShell/releases/download/v{PWSH_VERSION}"
PWSH_TARBALL = f"powershell-{PWSH_VERSION}-linux-x64.tar.gz"


def find_pwsh() -> str | None:
    env = os.environ.get("MU_PWSH")
    if env and Path(env).is_file():
        return env
    if (PWSH_DIR / "pwsh").is_file():
        return str(PWSH_DIR / "pwsh")
    return shutil.which("pwsh")


PWSH = find_pwsh()
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 not available (run the network test to fetch it)")


def ps_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A private HOME and XDG dirs: pwsh keeps caches there, never in the user's home."""
    home = tmp_path / "pshome"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CONFIG_HOME": str(home / ".config"),
        "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1", "LANG": "C.UTF-8",
    }
    env.update(extra)
    return env


def run_ps(tmp_path: Path, args: list[str], env: dict[str, str] | None = None, check: bool = True,
           timeout: float = 120) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", *args], capture_output=True, text=True,
                         env=env or ps_env(tmp_path), timeout=timeout)
    if check and out.returncode != 0:
        raise AssertionError(f"pwsh {args[:3]} -> {out.returncode}\nstdout:{out.stdout}\nstderr:{out.stderr}")
    return out


def last_json(text: str):
    return json.loads(text.strip().splitlines()[-1])


def ps_quote(s: str | Path) -> str:
    return "'" + str(s).replace("'", "''") + "'"


# -- static checks (no PowerShell needed) -----------------------------------------------------------------------------


def test_ps1_launcher_passes_the_same_key_options_as_bash():
    bash, ps1 = BASH.read_text(), PS1.read_text()
    exec_line = bash[bash.rindex('exec "$MPV_BIN"'):]
    options = set(re.findall(r"(--[a-z-]+=)", exec_line))
    assert options == {"--config-dir=", "--input-ipc-server=", "--watch-later-dir="}
    for token in [*options, "--player-operation-mode=pseudo-gui", "--idle=yes", "--player-operation-mode=",
                  "--no-idle", "--start=", "mpv-uos://download?", "mpv-uos://open?", "MPV_UOS_ROOT",
                  "MPV_UOS_DATA_DIR", "MPV_UOS_SOCKET", "MPV_UOS_MPV", "PYTHONPATH", "watch_later", "mpv-config",
                  "^[0-9]+(\\.[0-9]+)?$"]:
        assert token in bash, token
        assert token in ps1, f"{token} missing in bin/mpv-uos.ps1"
    assert "--\\{" in bash and "--\\}" in bash and "'--{'" in ps1 and "'--}'" in ps1   # per-file option groups
    assert "-m mpvd link --root" in bash and "'-m', 'mpvd', 'link', '--root'" in ps1
    # IPC through a named pipe (man mpv 0.41, --input-ipc-server: "On Windows, named pipes are used ... \\.\pipe\<name>")
    assert "'\\\\.\\pipe\\mpv-uos-'" in ps1
    assert ".venv\\Scripts\\python.exe" in ps1


def test_cmd_wrapper_calls_the_ps1():
    raw = CMD.read_bytes()
    assert raw.endswith(b"\r\n") and raw.count(b"\r\n") == 1          # one line, CRLF for cmd.exe
    line = raw.decode("ascii").strip()
    assert line.startswith("@powershell.exe -NoProfile -ExecutionPolicy Bypass -File ")
    assert '"%~dp0mpv-uos.ps1" %*' in line


@pytest.mark.parametrize("script", [PS1, INSTALL], ids=lambda p: p.name)
def test_ps1_avoids_powershell7_only_syntax(script: Path):
    """Windows 10/11 ship Windows PowerShell 5.1: no ??, ?., &&/||, ternaries, $IsWindows, -AsHashtable, -Parallel."""
    code = "\n".join(line for line in script.read_text().splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r"'[^'\n]*'", "''", code)                            # single-quoted strings are inert
    code = re.sub(r'"[^"\n]*"', '""', code)
    for bad in (r"\?\?", r"\?\.", r"&&", r"\|\|", r"\$IsWindows", r"\$IsLinux", r"-AsHashtable", r"-Parallel",
                r"\)\s*\?\s*[^:\n]+\s:\s", r"\bclean\s*\{"):
        assert not re.search(bad, code), f"{script.name}: PowerShell-7-only syntax {bad!r}"


def test_vendor_lock_pins_the_windows_binaries():
    lock = dict(line.split("=", 1) for line in (ROOT / "vendor.lock").read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)
    for key in ("YTDLP_EXE_SHA256", "WHISPER_WIN_SHA256", "DENO_SHA256_x86_64_windows", "UOSC_ZIP_SHA256"):
        assert re.fullmatch(r"[0-9a-f]{64}", lock[key]), key
    assert lock["WHISPER_WIN_URL"] == (f"https://github.com/ggml-org/whisper.cpp/releases/download/"
                                       f"{lock['WHISPER_WIN_VERSION']}/whisper-bin-x64.zip")
    install = INSTALL.read_text()
    for key in ("YTDLP_VERSION", "YTDLP_EXE_SHA256", "WHISPER_WIN_URL", "WHISPER_WIN_SHA256", "DENO_BASE_URL",
                "DENO_SHA256_x86_64_windows", "UOSC_ZIP_URL", "UOSC_ZIP_SHA256", "SHA2-256SUMS"):
        assert key in install, key


# -- Python side of the Windows port (no PowerShell needed) ---------------------------------------------------------


def test_whisper_binary_gets_exe_suffix_on_windows(tmp_path, monkeypatch):
    from mpvd.asr import models

    bin_dir = tmp_path / "vendor" / "whisper" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "whisper-cli.exe"
    exe.write_bytes(b"MZ")
    exe.chmod(0o755)
    monkeypatch.delenv("MPV_UOS_WHISPER_BIN", raising=False)
    monkeypatch.setattr(models.sys, "platform", "win32")
    assert models.find_binary("whisper-cli", tmp_path) == exe
    monkeypatch.setattr(models.sys, "platform", "linux")
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert models.find_binary("whisper-cli", tmp_path) is None


def test_ytdlp_updater_asset_is_the_exe_on_windows(tmp_path, monkeypatch):
    """The daily updater must install the native yt-dlp.exe where ytdl_hook and vendor_path() look for it on Windows,
    not the zipimport build under an .exe name."""
    from mpvd.ytdl import binary

    monkeypatch.delenv("MPV_UOS_VENDOR_BIN", raising=False)
    assert binary.YtdlpUpdater(None, tmp_path / "x").asset_name == "yt-dlp"  # type: ignore[arg-type]
    monkeypatch.setattr(binary.sys, "platform", "win32")
    assert binary.YtdlpUpdater(None, tmp_path / "x").asset_name == "yt-dlp.exe"  # type: ignore[arg-type]
    assert binary.vendor_path(tmp_path).name == "yt-dlp.exe"
    assert binary.YtdlpUpdater(None, tmp_path / "x", asset_name="yt-dlp").asset_name == "yt-dlp"  # type: ignore[arg-type]


def test_spawn_lock_uses_msvcrt_on_windows(tmp_path, monkeypatch):
    """mpvd ensure: two players starting together must not both spawn a daemon (on Windows a second server on the same
    pipe name would not fail like a second bind() does)."""
    import types

    import mpvd.__main__ as main
    from mpvd.config import Settings

    calls: list[tuple[int, int]] = []
    busy = {"n": 2}

    def locking(fd: int, mode: int, nbytes: int) -> None:
        calls.append((mode, nbytes))
        if mode == 2 and busy["n"] > 0:          # LK_NBLCK while another process holds it
            busy["n"] -= 1
            raise OSError(36, "Resource deadlock avoided")

    fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    settings = Settings(runtime_dir=tmp_path / "run", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data")
    with main._spawn_lock(settings):
        assert calls == [(2, 1), (2, 1), (2, 1)]
    assert calls[-1] == (0, 1)
    # never able to lock: gives up after the timeout and goes on unlocked (no unlock call)
    calls.clear()
    busy["n"] = 10**9
    clock = iter(range(0, 10**6, 10))
    monkeypatch.setattr(main.time, "monotonic", lambda: next(clock))
    with main._spawn_lock(settings):
        pass
    assert calls and all(m == 2 for m, _ in calls)


# -- PowerShell: parser -----------------------------------------------------------------------------------------------


@needs_pwsh
@pytest.mark.parametrize("script", [PS1, INSTALL], ids=lambda p: p.name)
def test_ps1_parses_without_errors(tmp_path, script: Path):
    code = (f"$t = $null; $e = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"{ps_quote(script)}, [ref]$t, [ref]$e); "
            "@($e | ForEach-Object { $_.Extent.StartLineNumber.ToString() + ': ' + $_.Message }) | ConvertTo-Json -Compress")
    out = run_ps(tmp_path, ["-Command", code]).stdout.strip()
    assert out in ("", "[]", "null"), out


# -- PowerShell: launcher ---------------------------------------------------------------------------------------------


def bash_launcher_args(tmp_path: Path, *args: str) -> list[str]:
    """bin/mpv-uos with a fake mpv printing its argv (like tests/test_launcher.py)."""
    fake = tmp_path / "fake-mpv"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    fake.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MPV_UOS_MPV": str(fake),
           "MPV_UOS_RUNTIME_DIR": str(tmp_path), "MPV_UOS_DATA_DIR": str(tmp_path / "data")}
    out = subprocess.run([str(BASH), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.splitlines()


def ps_launcher(tmp_path: Path, *args: str, dry: bool = True, check: bool = True, **env: str):
    e = ps_env(tmp_path, **{"MPV_UOS_DATA_DIR": str(tmp_path / "data"), "MPV_UOS_MPV": str(tmp_path / "fake-mpv"),
                            **env})
    return run_ps(tmp_path, ["-File", str(PS1), *(["-DryRun"] if dry else []), *args], env=e, check=check)


PIPE_RE = re.compile(r"^\\\\\.\\pipe\\mpv-uos-\d+-[0-9a-f]{8}$")

CASES = [
    [],
    ["--fs"],
    ["video.mkv"],
    ["--", "-raro.mkv"],
    ["--idle=yes"],
    ["--idle=once", "video.mkv"],
    ["C:\\Vídeos\\mi peli (2024).mkv", "--start=10"],
    ["--player-operation-mode=pseudo-gui", "--", "mpv-uos://open?path=%2Fhome%2Fser%2FV%C3%ADdeos%2FMi%20peli%3A%20parte"
     "%201%20(2024)%20%26%20m%C3%A1s.mkv&t=83.4", "/otro.mkv", "mpv-uos://open?path=https%3A%2F%2Fexample.com%2Fv%3Fa"
     "%3D1%26b%3D2"],
    ["mpv-uos://open?path=%2Fx.mkv&t=1%3Bquit"],
]


@needs_pwsh
@pytest.mark.parametrize("args", CASES, ids=[str(i) for i in range(len(CASES))])
def test_ps1_dry_run_builds_the_same_mpv_command_as_bash(tmp_path, args):
    expected = bash_launcher_args(tmp_path, *args)
    got = last_json(ps_launcher(tmp_path, *args).stdout)
    assert got["mpvd"] is None and PIPE_RE.match(got["pipe"]), got
    mpv = got["mpv"]
    assert mpv[0] == str(tmp_path / "fake-mpv")
    ipc = [i for i, a in enumerate(mpv) if a.startswith("--input-ipc-server=")]
    assert ipc == [2] and mpv[2] == "--input-ipc-server=" + got["pipe"]
    bash_ipc = next(i for i, a in enumerate(expected) if a.startswith("--input-ipc-server="))
    assert mpv[1:2] + mpv[3:] == expected[:bash_ipc] + expected[bash_ipc + 1:]
    assert got["env"] == {"MPV_UOS_ROOT": str(ROOT), "MPV_UOS_DATA_DIR": str(tmp_path / "data")}


@needs_pwsh
def test_ps1_pipe_names_are_unique_and_the_inherited_socket_is_ignored(tmp_path):
    a = last_json(ps_launcher(tmp_path, "x.mkv", MPV_UOS_SOCKET="\\\\.\\pipe\\other").stdout)
    b = last_json(ps_launcher(tmp_path, "x.mkv").stdout)
    assert a["pipe"] != b["pipe"] and "other" not in json.dumps(a)
    assert not (tmp_path / "data").exists()          # -DryRun creates nothing


@needs_pwsh
def test_ps1_download_links_go_to_mpvd(tmp_path):
    link = "mpv-uos://download?url=https%3A%2F%2Fexample.com%2Fv&preset=audio"
    got = last_json(ps_launcher(tmp_path, link, "mpv-uos://download?url=x", PYTHONPATH="/extra").stdout)
    assert got["mpv"] is None
    py = got["mpvd"][0]
    assert py in (str(ROOT / ".venv" / "bin" / "python"), "python")
    assert got["mpvd"][1:] == ["-m", "mpvd", "link", "--root", str(ROOT), link, "mpv-uos://download?url=x"]
    assert got["env"]["PYTHONPATH"] == f"{ROOT}{os.pathsep}/extra"


@needs_pwsh
def test_ps1_really_runs_mpv_with_those_arguments(tmp_path):
    """Without -DryRun: the call operator hands every argument over intact (spaces, accents, --{ groups)."""
    args = CASES[7]
    dry = last_json(ps_launcher(tmp_path, *args).stdout)["mpv"]
    bash_launcher_args(tmp_path)                      # writes the fake mpv
    out = ps_launcher(tmp_path, *args, dry=False).stdout.splitlines()
    assert out[0].startswith("--config-dir=") and PIPE_RE.match(out[1].split("=", 1)[1])
    assert out[:1] + out[2:] == dry[1:2] + dry[3:]
    assert (tmp_path / "data" / "watch_later").is_dir()


@needs_pwsh
def test_ps1_without_mpv_explains_how_to_install_it(tmp_path):
    out = ps_launcher(tmp_path, "x.mkv", dry=False, check=False, MPV_UOS_MPV="mpv-that-does-not-exist")
    assert out.returncode == 1
    assert "winget install mpv" in out.stderr and "scoop install mpv" in out.stderr


# -- PowerShell: installer --------------------------------------------------------------------------------------------


def install_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return ps_env(tmp_path, MPV_UOS_INSTALL_DIR=str(tmp_path / "inst"), MPV_UOS_START_MENU_DIR=str(tmp_path / "menu"),
                  **extra)


def tools_available(*names: str) -> bool:
    return all(shutil.which(n) for n in names)


@needs_pwsh
def test_install_dry_run_plans_everything_and_changes_nothing(tmp_path):
    if not tools_available("mpv", "ffmpeg", "ffprobe"):
        pytest.skip("mpv/ffmpeg/ffprobe not on PATH")
    out = run_ps(tmp_path, ["-File", str(INSTALL), "--dry-run", "--no-sync", "--whisper"], env=install_env(tmp_path))
    plan = last_json(out.stdout)
    assert plan["mode"] == "install" and plan["install_dir"] == str(tmp_path / "inst")
    acts = {a["action"]: a for a in plan["actions"]}
    assert {"vendor-ziggy", "vendor-yt-dlp", "vendor-whisper", "write-launcher", "create-shortcut",
            "register-protocol", "write-state"} <= set(acts)
    assert "uv-sync" not in acts
    ps1 = str(ROOT / "bin" / "mpv-uos.ps1")
    assert acts["create-shortcut"]["target"] == str(tmp_path / "menu" / f"{APP}.lnk")
    assert acts["create-shortcut"]["detail"].endswith(
        f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1}" -Gui')
    assert acts["register-protocol"]["detail"].endswith(f'-File "{ps1}" -Gui "%1"')
    assert acts["register-protocol"]["target"] == "HKCU:\\Software\\Classes\\mpv-uos"
    assert acts["vendor-yt-dlp"]["target"] == str(ROOT / "vendor" / "bin" / "yt-dlp.exe")
    assert not (tmp_path / "inst").exists() and not (tmp_path / "menu").exists()


@needs_pwsh
def test_install_then_uninstall_in_a_temporary_folder(tmp_path):
    if not tools_available("mpv", "ffmpeg", "ffprobe"):
        pytest.skip("mpv/ffmpeg/ffprobe not on PATH")
    env = install_env(tmp_path)
    run_ps(tmp_path, ["-File", str(INSTALL), "-NoSync", "-NoVendor"], env=env)
    launcher = tmp_path / "inst" / "bin" / "mpv-uos.cmd"
    raw = launcher.read_bytes()
    assert raw.isascii() and b"\r\n" in raw and b"generated by MPV-UOS tools/install.ps1" in raw
    text = raw.decode()
    ps1 = str(ROOT / "bin" / "mpv-uos.ps1")
    assert f'if exist "{ps1}" goto run' in text and f'-File "{ps1}" %*' in text
    state = json.loads((tmp_path / "inst" / "install.json").read_text())
    assert state["root"] == str(ROOT) and state["launcher"] == str(launcher)
    assert state["shortcut"] == str(tmp_path / "menu" / f"{APP}.lnk") and state["protocol"] is True
    # a shortcut we did not create (not in install.json) is left alone; ours is removed
    (tmp_path / "menu").mkdir()
    foreign = tmp_path / "menu" / "Otro.lnk"
    foreign.write_text("x")
    Path(state["shortcut"]).write_text("ours")
    out = run_ps(tmp_path, ["-File", str(INSTALL), "--uninstall"], env=env)
    assert "desinstalado" in out.stdout
    assert not (tmp_path / "inst").exists() and not Path(state["shortcut"]).exists() and foreign.exists()


@needs_pwsh
def test_install_never_overwrites_a_foreign_launcher(tmp_path):
    if not tools_available("mpv", "ffmpeg", "ffprobe"):
        pytest.skip("mpv/ffmpeg/ffprobe not on PATH")
    env = install_env(tmp_path)
    launcher = tmp_path / "inst" / "bin" / "mpv-uos.cmd"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("@echo mine\r\n")
    out = run_ps(tmp_path, ["-File", str(INSTALL), "-NoSync", "-NoVendor"], env=env, check=False)
    assert out.returncode == 1 and "no se sobrescribe" in out.stderr
    run_ps(tmp_path, ["-File", str(INSTALL), "-Uninstall"], env=env)
    assert launcher.read_bytes() == b"@echo mine\r\n"


@needs_pwsh
def test_install_without_mpv_explains_how_to_get_it(tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    out = run_ps(tmp_path, ["-File", str(INSTALL), "-NoVendor"], env=install_env(tmp_path, PATH=str(empty)),
                 check=False)
    assert out.returncode == 1
    for hint in ("winget install mpv", "scoop install mpv", "winget install Gyan.FFmpeg", "winget install astral-sh.uv"):
        assert hint in out.stderr, hint


@pytest.fixture()
def release_server(tmp_path):
    """A local HTTP server standing in for GitHub release downloads."""
    root = tmp_path / "www"
    root.mkdir()
    handler = functools.partial(_QuietHandler, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def call_install_fn(tmp_path: Path, code: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_ps(tmp_path, ["-Command", f"$ErrorActionPreference = 'Stop'; . {ps_quote(INSTALL)}; {code}"],
                  check=check)


@needs_pwsh
def test_install_ytdlp_exe_is_checked_against_the_release_sums(tmp_path, release_server):
    www, base = release_server
    body = b"MZ fake yt-dlp.exe"
    (www / "yt-dlp.exe").write_bytes(body)
    (www / "SHA2-256SUMS").write_text(f"{'0' * 64}  yt-dlp\n{sha(body)}  yt-dlp.exe\n{'1' * 64}  yt-dlp_x86.exe\n")
    vend = tmp_path / "vendor"
    lock = f"@{{ YTDLP_VERSION = '2099.01.01' }}"

    def install(extra: str = "", lock_expr: str = lock) -> subprocess.CompletedProcess[str]:
        return call_install_fn(tmp_path, f"Install-MuYtDlp -Lock {lock_expr} -VendorDir {ps_quote(vend)} "
                                         f"-BaseUrl {ps_quote(base)} {extra}", check=False)

    out = install()
    assert out.returncode == 0 and out.stdout.strip() == "downloaded", out.stderr
    assert (vend / "bin" / "yt-dlp.exe").read_bytes() == body
    assert (vend / "bin" / "yt-dlp.exe.version").read_bytes() == b"2099.01.01\n"
    assert install().stdout.strip() == "present"
    assert install("-Force").stdout.strip() == "cached"
    # the lock's own pin must agree with the release's sums
    out = install("-Force", f"@{{ YTDLP_VERSION = '2099.01.01'; YTDLP_EXE_SHA256 = '{'2' * 64}' }}")
    assert out.returncode != 0 and "YTDLP_EXE_SHA256" in out.stderr
    # tampered download: nothing is installed
    shutil.rmtree(vend)
    (www / "yt-dlp.exe").write_bytes(b"MZ evil")
    out = install()
    assert out.returncode != 0 and "SHA-256 incorrecto" in out.stderr
    assert not (vend / "bin" / "yt-dlp.exe").exists() and not list((vend / "dl").glob("yt-dlp-*.exe*"))


@needs_pwsh
def test_install_whisper_ziggy_and_deno_unpack_only_what_is_needed(tmp_path, release_server):
    www, base = release_server
    wzip = make_zip({"Release/whisper-cli.exe": b"MZ cli", "Release/whisper-server.exe": b"MZ srv",
                     "Release/ggml.dll": b"dll", "Release/whisper.dll": b"dll2", "Release/bench.exe": b"MZ bench",
                     "Release/SDL2.dll": b"sdl"})
    uzip = make_zip({"scripts/uosc/main.lua": b"-- uosc", "scripts/uosc/bin/ziggy-windows.exe": b"MZ ziggy",
                     "scripts/uosc/bin/ziggy-linux": b"ELF"})
    dzip = make_zip({"deno.exe": b"MZ deno"})
    for name, data in (("whisper-bin-x64.zip", wzip), ("uosc.zip", uzip), ("deno-x86_64-pc-windows-msvc.zip", dzip)):
        (www / name).write_bytes(data)
    vend, conf = tmp_path / "vendor", tmp_path / "mpv-config"
    lock = (f"@{{ WHISPER_WIN_VERSION = 'b1'; WHISPER_WIN_URL = '{base}/whisper-bin-x64.zip'; "
            f"WHISPER_WIN_SHA256 = '{sha(wzip)}'; UOSC_VERSION = '9.9'; UOSC_ZIP_URL = '{base}/uosc.zip'; "
            f"UOSC_ZIP_SHA256 = '{sha(uzip)}'; DENO_VERSION = 'v9'; DENO_BASE_URL = '{base}'; "
            f"DENO_SHA256_x86_64_windows = '{sha(dzip)}' }}")
    code = (f"$l = {lock}; Install-MuWhisper -Lock $l -VendorDir {ps_quote(vend)}; "
            f"Install-MuZiggy -Lock $l -VendorDir {ps_quote(vend)} -ConfigDir {ps_quote(conf)}; "
            f"Install-MuDeno -Lock $l -VendorDir {ps_quote(vend)}")
    assert call_install_fn(tmp_path, code).stdout.split() == ["downloaded"] * 3
    assert sorted(p.name for p in (vend / "whisper" / "bin").iterdir()) == [
        "SDL2.dll", "ggml.dll", "whisper-cli.exe", "whisper-server.exe", "whisper.dll"]
    assert [p.name for p in (conf / "scripts" / "uosc" / "bin").iterdir()] == ["ziggy-windows.exe"]
    assert (vend / "bin" / "deno.exe").read_bytes() == b"MZ deno"
    assert call_install_fn(tmp_path, code).stdout.split() == ["cached"] * 3
    # a wrong pin fails before anything is unpacked
    shutil.rmtree(vend / "whisper")
    bad = lock.replace(sha(wzip), "3" * 64)
    out = call_install_fn(tmp_path, f"Install-MuWhisper -Lock {bad} -VendorDir {ps_quote(vend)}", check=False)
    assert out.returncode != 0 and "SHA-256 incorrecto" in out.stderr and not (vend / "whisper" / "bin").exists()


@needs_pwsh
def test_sums_parser_and_vendor_lock_reader(tmp_path):
    code = (f"$l = Read-MuVendorLock {ps_quote(ROOT / 'vendor.lock')}; "
            "$s = \"abc  x`n\" + ('a' * 64) + ' *yt-dlp.exe' + \"`r`n\" + ('b' * 64) + '  yt-dlp'; "
            "@($l['YTDLP_VERSION'], $l['WHISPER_WIN_VERSION'], (Get-MuSumsEntry $s 'yt-dlp.exe'), "
            "(Get-MuSumsEntry $s 'yt-dlp'), (Get-MuSumsEntry $s 'nope')) | ConvertTo-Json -Compress")
    got = json.loads(call_install_fn(tmp_path, code).stdout)
    lock = (ROOT / "vendor.lock").read_text()
    assert f"YTDLP_VERSION={got[0]}\n" in lock and f"WHISPER_WIN_VERSION={got[1]}\n" in lock
    assert got[2:] == ["a" * 64, "b" * 64, ""]


# -- network: the portable PowerShell itself and the real Windows downloads -----------------------------------------


@pytest.mark.network
def test_fetch_portable_pwsh(tmp_path):
    """PowerShell 7 for linux-x64 into <checkout>/.cache/pwsh (verified with the release's hashes.sha256)."""
    if (PWSH_DIR / "pwsh").is_file():
        pytest.skip(f"already in {PWSH_DIR}")
    dl = ROOT / ".cache" / "dl"
    dl.mkdir(parents=True, exist_ok=True)
    raw = urllib.request.urlopen(f"{PWSH_RELEASE}/hashes.sha256", timeout=30).read()
    sums = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")  # UTF-16 LE
    expected = next(line.split()[0].lower() for line in sums.splitlines() if line.strip().endswith(PWSH_TARBALL))
    tarball = dl / PWSH_TARBALL
    if not tarball.is_file() or sha(tarball.read_bytes()) != expected:
        urllib.request.urlretrieve(f"{PWSH_RELEASE}/{PWSH_TARBALL}", tarball)
    assert sha(tarball.read_bytes()) == expected
    PWSH_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tf:
        tf.extractall(PWSH_DIR, filter="tar")
    (PWSH_DIR / "pwsh").chmod(0o755)
    assert subprocess.run([str(PWSH_DIR / "pwsh"), "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
                          capture_output=True, text=True, timeout=120, env=ps_env(tmp_path)).stdout.strip() == "7"


@pytest.mark.network
@needs_pwsh
def test_real_windows_downloads_verify(tmp_path):
    """The real yt-dlp.exe (+ SHA2-256SUMS of the pinned release), whisper-bin-x64.zip and deno for Windows."""
    vend = tmp_path / "vendor"
    code = (f"$l = Read-MuVendorLock {ps_quote(ROOT / 'vendor.lock')}; "
            f"Install-MuYtDlp -Lock $l -VendorDir {ps_quote(vend)}; Install-MuWhisper -Lock $l -VendorDir {ps_quote(vend)}; "
            f"Install-MuDeno -Lock $l -VendorDir {ps_quote(vend)}")
    assert call_install_fn(tmp_path, code).stdout.split() == ["downloaded"] * 3
    for exe in (vend / "bin" / "yt-dlp.exe", vend / "whisper" / "bin" / "whisper-cli.exe", vend / "bin" / "deno.exe"):
        assert exe.read_bytes()[:2] == b"MZ", exe
    assert (vend / "whisper" / "bin" / "whisper.dll").is_file() and (vend / "whisper" / "bin" / "ggml.dll").is_file()
