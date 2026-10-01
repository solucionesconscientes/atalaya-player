"""H25 · the Cloudflare quick tunnel of the share rooms (ADR-068), against a fake cloudflared: no network, nothing of
this machine published anywhere.

What matters here: the address is read from what cloudflared prints, the room link uses it, the tunnel is only opened when
it is asked for, a failure leaves the room working in the LAN, and the process really dies when the room closes (that is
the whole promise: nothing reachable from the internet for longer than the room).
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import pytest

from mpvd.config import Settings
from mpvd.server import MpvdServer
from mpvd.share.tunnel import CloudflaredTunnel, TunnelError, find_cloudflared

FAKE = Path(__file__).parent / "fixtures" / "share" / "fake_cloudflared.py"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_the_command_is_a_quick_tunnel_without_account_or_autoupdate():
    t = CloudflaredTunnel(Path("/usr/bin/cloudflared"))
    assert t.args(8791) == ["/usr/bin/cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8791"]
    assert t.name == "cloudflared"


def test_start_reads_the_address_and_stop_kills_the_process(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    args_file = tmp_path / "argv"
    monkeypatch.setenv("FAKE_CF_PIDFILE", str(pid_file))
    monkeypatch.setenv("FAKE_CF_ARGSFILE", str(args_file))
    monkeypatch.setenv("FAKE_CF_URL", "https://dos-palabras-azar.trycloudflare.com")
    t = CloudflaredTunnel(FAKE, start_timeout=20)

    async def go():
        # one loop for the whole life of the process, as it is in the daemon
        url = await t.start(8791)
        assert url == "https://dos-palabras-azar.trycloudflare.com" and t.url == url
        assert "--url http://127.0.0.1:8791" in args_file.read_text(encoding="utf-8")
        pid = int(pid_file.read_text(encoding="utf-8"))
        assert alive(pid)
        assert await t.start(8791) == url            # asking again reuses the one already open
        await t.stop()
        await t.stop()                               # idempotent
        return pid

    pid = asyncio.run(go())
    assert wait_gone(pid), "cloudflared sigue vivo tras cerrar el túnel"
    assert t.url is None


def test_a_cloudflared_that_dies_is_a_clear_error_with_its_own_words(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CF_URL", "")
    monkeypatch.setenv("FAKE_CF_EXIT", "1")
    with pytest.raises(TunnelError) as exc:
        asyncio.run(CloudflaredTunnel(FAKE, start_timeout=20).start(8791))
    assert "sin dar una dirección" in str(exc.value)
    assert "failed to connect to the edge" in str(exc.value)     # the tail of its output, to know what happened


def test_a_cloudflared_that_says_nothing_times_out(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CF_DELAY", "30")
    t = CloudflaredTunnel(FAKE, start_timeout=1.0)
    with pytest.raises(TunnelError, match="no dio una dirección a tiempo"):
        asyncio.run(t.start(8791))
    assert t.url is None and t._proc is None


def test_find_cloudflared_prefers_the_env_var_then_vendor(tmp_path, monkeypatch):
    monkeypatch.setenv("MPV_UOS_CLOUDFLARED", str(FAKE))
    assert find_cloudflared(None) == FAKE

    monkeypatch.delenv("MPV_UOS_CLOUDFLARED")
    root = tmp_path / "proyecto"
    (root / "vendor" / "bin").mkdir(parents=True)
    vendored = root / "vendor" / "bin" / "cloudflared"
    vendored.write_text("#!/bin/sh\n", encoding="utf-8")
    vendored.chmod(0o755)
    assert find_cloudflared(root) == vendored


def make_server(tmp_path: Path) -> MpvdServer:
    return MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                               idle_timeout=0, workers=1))


def test_a_room_only_gets_a_tunnel_when_it_asks_for_one(tmp_path, monkeypatch):
    """The default is the local network: nothing of this house goes out unless «desde internet» is chosen."""
    monkeypatch.setenv("MPV_UOS_CLOUDFLARED", str(FAKE))
    share = make_server(tmp_path).share
    assert share.status()["tunnel_available"] is True
    assert share.status()["public_url"] is None and share.status()["lan_only"] is True
    assert share.tunnel is None


def test_without_cloudflared_the_room_still_works_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("MPV_UOS_CLOUDFLARED", str(tmp_path / "no-existe"))
    monkeypatch.setenv("PATH", str(tmp_path))
    share = make_server(tmp_path).share
    share.server.root = tmp_path / "sin-vendor"      # a checkout without vendor/bin/cloudflared
    assert share.status()["tunnel_available"] is False
    assert share._make_tunnel() is None
    assert "falta cloudflared" in share.tunnel_error and "sigue funcionando en tu red" in share.tunnel_error


# -- a real room, with the fake cloudflared in front of it ----------------------------------------------------------


@pytest.fixture
def tunnel_env(daemon_env, tmp_path):
    """A daemon whose cloudflared is the fake one, and the pid file it writes."""
    pid_file = tmp_path / "cf.pid"
    daemon_env.extra_env.update({
        "MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PUBLIC_HOST": "127.0.0.1", "MPVD_SHARE_PORT": "0",
        "MPV_UOS_CLOUDFLARED": str(FAKE), "FAKE_CF_PIDFILE": str(pid_file),
        "FAKE_CF_URL": "https://sala-de-ser.trycloudflare.com", "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
    })
    from tests.conftest import start_mpv

    h = start_mpv(daemon_env.runtime_dir,
                  ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5",
                   "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env, pid_file
    finally:
        h.stop()


def test_the_room_link_goes_through_the_tunnel_and_it_dies_with_the_room(tunnel_env, media_dir):
    """H25 · «desde internet»: the link handed out is the public one, and the tunnel lives exactly as long as the room."""
    h, d, pid_file = tunnel_env
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    res = d.call("share.create", {"internet": True})
    assert res["url"].startswith("https://sala-de-ser.trycloudflare.com/s/")
    st = d.call("share.status")
    assert st["public_url"] == "https://sala-de-ser.trycloudflare.com" and st["lan_only"] is False
    assert st["tunnel"] == "cloudflared" and st["tunnel_error"] == ""
    assert st["firewall"] is None            # nothing to open in the router: the tunnel goes outwards
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert alive(pid)

    d.call("share.close")
    assert wait_gone(pid), "el túnel sigue abierto tras cerrar la sala"
    st = d.call("share.status")
    assert st["open"] is False and st["public_url"] is None and st["lan_only"] is True


def test_a_room_without_internet_never_opens_a_tunnel(tunnel_env, media_dir):
    h, d, pid_file = tunnel_env
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    res = d.call("share.create")             # the default: only inside the house
    assert res["url"].startswith("http://127.0.0.1:")
    st = d.call("share.status")
    assert st["public_url"] is None and st["lan_only"] is True and st["tunnel"] is None
    assert not pid_file.exists(), "se arrancó cloudflared sin pedirlo"
    d.call("share.close")


# -- the real thing, only when explicitly asked for --------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("MPV_UOS_TEST_TUNNEL") != "1",
                    reason="abre un túnel real a internet: MPV_UOS_TEST_TUNNEL=1 para probarlo")
def test_a_real_quick_tunnel_serves_what_is_behind_it(tmp_path):
    """Un túnel de verdad delante de un servidor local que solo sirve «ok»: nada del equipo se publica.

    El nombre del túnel puede no resolverse en el resolutor de esta máquina (pasa en entornos con DNS restringido), así
    que se resuelve por DoH y se pide con --resolve: lo que se comprueba es el túnel, no el DNS de aquí.
    """
    import http.server
    import json
    import subprocess
    import threading

    from mpvd.share.tunnel import find_cloudflared

    binary = find_cloudflared(Path.cwd())
    if binary is None:
        pytest.skip("falta cloudflared (MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh)")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"ok-mpv-uos-tunel"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN002
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    async def go():
        t = CloudflaredTunnel(binary)
        url = await t.start(httpd.server_address[1])
        host = url.split("//", 1)[1]
        try:
            ip = None
            for _ in range(8):
                out = subprocess.run(["curl", "-sS", "--max-time", "10", "-H", "accept: application/dns-json",
                                      f"https://cloudflare-dns.com/dns-query?name={host}&type=A"],
                                     capture_output=True, text=True, check=False).stdout
                answers = [a for a in json.loads(out or "{}").get("Answer", []) if a.get("type") == 1]
                if answers:
                    ip = answers[0]["data"]
                    break
                await asyncio.sleep(4)
            assert ip, f"la dirección {host} no se resolvió"
            got = subprocess.run(["curl", "-sS", "--max-time", "20", "--resolve", f"{host}:443:{ip}", url],
                                 capture_output=True, text=True, check=False)
            assert got.stdout.strip() == "ok-mpv-uos-tunel", (got.stdout[:200], got.stderr[:200])
            return t._proc.pid if t._proc else None
        finally:
            await t.stop()

    try:
        pid = asyncio.run(go())
    finally:
        httpd.shutdown()
    assert pid and wait_gone(pid), "cloudflared sigue vivo tras cerrar el túnel"
