"""H25 · the Cloudflare quick tunnel of the share rooms (ADR-068), against a fake cloudflared: no network, nothing of
this machine published anywhere.

What matters here: the address is read from what cloudflared prints, the room link uses it, the tunnel is only opened when
it is asked for, a failure leaves the room working in the LAN, and the process really dies when the room closes (that is
the whole promise: nothing reachable from the internet for longer than the room).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.server
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mpvd.config import Settings
from mpvd.server import MpvdServer
from mpvd.share import tunnel as tunnel_mod
from mpvd.share.tunnel import CloudflaredDownloadError, CloudflaredTunnel, TunnelError, find_cloudflared

ROOT = Path(__file__).resolve().parents[1]
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
    daemon_pid = tmp_path / "cloudflared.pid"
    t = CloudflaredTunnel(FAKE, start_timeout=20, pid_file=daemon_pid)

    async def go():
        # one loop for the whole life of the process, as it is in the daemon
        url = await t.start(8791)
        assert url == "https://dos-palabras-azar.trycloudflare.com" and t.url == url
        assert "--url http://127.0.0.1:8791" in args_file.read_text(encoding="utf-8")
        pid = int(pid_file.read_text(encoding="utf-8"))
        assert alive(pid)
        assert daemon_pid.read_text(encoding="utf-8").strip() == str(pid)   # F1: the sweep needs this
        assert await t.start(8791) == url            # asking again reuses the one already open
        await t.stop()
        assert not daemon_pid.exists(), "el fichero de PID queda huérfano al cerrar"
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


def test_without_cloudflared_the_room_still_works_and_can_download_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MPV_UOS_CLOUDFLARED", str(tmp_path / "no-existe"))
    monkeypatch.setenv("PATH", str(tmp_path))
    share = make_server(tmp_path).share
    share.server.root = tmp_path / "sin-vendor"      # a package without the binary and without vendor.lock
    st = share.status()
    assert st["tunnel_available"] is False and st["tunnel_downloadable"] is False
    assert share._make_tunnel() is None
    # the old instruction is gone from the UI and from the error: F1 downloads it on demand
    assert "MU_VENDOR_CLOUDFLARED" not in share.tunnel_error


def esperar(d, cond, timeout: float = 30.0) -> dict:  # type: ignore[no-untyped-def]
    """share.status hasta que cumpla `cond` (el túnel ya no bloquea a share.create)."""
    fin = time.monotonic() + timeout
    st = d.call("share.status")
    while time.monotonic() < fin:
        if cond(st):
            return st
        time.sleep(0.2)
        st = d.call("share.status")
    raise AssertionError(f"share.status no llegó a cumplirlo: tunnel_state={st.get('tunnel_state')!r}")


# -- a real room, with the fake cloudflared in front of it ----------------------------------------------------------


@pytest.fixture
def tunnel_env(daemon_env, tmp_path):
    """A daemon whose cloudflared is the fake one, and the pid file it writes."""
    pid_file = tmp_path / "cf.pid"
    daemon_env.extra_env.update({
        "MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PUBLIC_HOST": "127.0.0.1", "MPVD_SHARE_PORT": "0",
        "MPV_UOS_CLOUDFLARED": str(FAKE), "FAKE_CF_PIDFILE": str(pid_file),
        "FAKE_CF_URL": "https://sala-de-ser.trycloudflare.com", "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
        # H51 · la dirección de pega no existe en internet, así que aquí no se comprueba que conteste; que se
        # espera a que conteste de verdad se prueba aparte (test_la_direccion_no_se_da_por_buena_hasta_que_contesta)
        "MPVD_SHARE_TUNNEL_PROBE": "0",
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

    # H51 · la sala se abre YA, con su dirección local, y el túnel se abre detrás: antes se esperaba aquí a
    # cloudflared y no aparecía nada durante los 6-8 s que tarda en hablar.
    res = d.call("share.create", {"internet": True})
    assert res["url"].startswith("http://127.0.0.1:")
    assert res["status"]["tunnel_state"] in ("starting", "warming", "ready")

    st = esperar(d, lambda s: s["tunnel_state"] == "ready")
    assert st["public_url"] == "https://sala-de-ser.trycloudflare.com" and st["lan_only"] is False
    assert st["tunnel"] == "cloudflared" and st["tunnel_error"] == ""
    assert st["firewall"] is None            # nothing to open in the router: the tunnel goes outwards
    # y a partir de ahí el enlace que se reparte YA es el público
    assert d.call("share.link")["url"].startswith("https://sala-de-ser.trycloudflare.com/s/")
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert alive(pid)

    d.call("share.close")
    assert wait_gone(pid), "el túnel sigue abierto tras cerrar la sala"
    st = d.call("share.status")
    assert st["open"] is False and st["public_url"] is None and st["lan_only"] is True
    assert st["tunnel_state"] == "off"


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


# -- H51 · la dirección no se da por buena hasta que contesta ---------------------------------------------------


def test_url_answers_distingue_enrutar_de_no_enrutar():
    """Cualquier respuesta HTTP vale, incluso un 404: lo que se comprueba es que el túnel LLEVA la petición, no
    lo que conteste. Una dirección que no enruta no contesta nada."""
    import http.server
    import threading

    from mpvd.share.service import url_answers

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"no")

        def log_message(self, *a):  # noqa: ANN002
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert url_answers(f"http://127.0.0.1:{port}/s/loquesea") is True
    finally:
        srv.shutdown()
    assert url_answers(f"http://127.0.0.1:{port}/s/loquesea") is False   # ya no hay nadie detrás
    assert url_answers("http://no-existe-esta-maquina.invalid/x") is False


def test_la_direccion_no_se_da_por_buena_hasta_que_contesta(daemon_env, tmp_path, media_dir):
    """El fallo que encontró Ser: «al compartir el enlace, tarda un min aprox en cargar». cloudflared entrega la
    dirección a los 6-8 s pero no enruta hasta ~65 s (medido tres veces el 2026-10-02), y nosotros la dábamos por
    buena en el primer momento: el enlace que se copiaba estaba MUERTO durante un minuto. Aquí la dirección de
    pega no contesta nunca, así que la sala tiene que quedarse en «abriendo» y acabar diciendo que no, sin llegar
    a presentarla nunca como pública."""
    pid_file = tmp_path / "cf.pid"
    daemon_env.extra_env.update({
        "MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PUBLIC_HOST": "127.0.0.1", "MPVD_SHARE_PORT": "0",
        "MPV_UOS_CLOUDFLARED": str(FAKE), "FAKE_CF_PIDFILE": str(pid_file),
        "FAKE_CF_URL": "https://esta-no-contesta-nunca.trycloudflare.com",
        "MPV_UOS_YTDLP_AUTO_UPDATE": "0", "MPVD_SHARE_TUNNEL_WARM": "6",   # 6 s en vez de 240
    })
    from tests.conftest import start_mpv

    h = start_mpv(daemon_env.runtime_dir,
                  ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5",
                   "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

        res = daemon_env.call("share.create", {"internet": True})
        assert res["url"].startswith("http://127.0.0.1:")      # la sala se abre ya, con lo que sí funciona
        st = esperar(daemon_env, lambda s: s["tunnel_state"] == "warming", timeout=20)
        assert st["pending_url"] == "https://esta-no-contesta-nunca.trycloudflare.com"
        assert st["public_url"] is None and st["lan_only"] is True
        # mientras no conteste, el enlace que se reparte NO es el que no sirve
        assert daemon_env.call("share.link")["url"].startswith("http://127.0.0.1:")

        st = esperar(daemon_env, lambda s: s["tunnel_state"] == "failed", timeout=40)
        assert st["public_url"] is None and "no ha llegado a contestar" in st["tunnel_error"]
        assert daemon_env.call("share.link")["url"].startswith("http://127.0.0.1:")

        daemon_env.call("share.close")
        assert wait_gone(int(pid_file.read_text(encoding="utf-8"))), "el túnel a medias sigue abierto"
    finally:
        h.stop()


# -- F1 · cloudflared bajo demanda, con hash y sin huérfanos ---------------------------------------------------------


def _linux_amd64() -> bool:
    return sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64")


def _asset_server(payload: bytes, support_range: bool = True):
    class Handler(http.server.BaseHTTPRequestHandler):
        ranges: list[str] = []

        def do_GET(self):  # noqa: N802
            data = payload
            header = self.headers.get("Range")
            type(self).ranges.append(header or "")
            if header and support_range:
                start = int(header.split("=", 1)[1].split("-", 1)[0])
                body = data[start:]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN002
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, Handler


def _write_lock(root: Path, base_url: str, sha: str, version: str = "test") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "vendor.lock").write_text(
        f"CLOUDFLARED_VERSION={version}\nCLOUDFLARED_BASE_URL={base_url}\n"
        f"CLOUDFLARED_SHA256_x86_64_linux={sha}\n",
        encoding="utf-8")


def test_en_un_paquete_manda_el_binario_que_viaja_dentro(tmp_path, monkeypatch):
    monkeypatch.setenv("MPV_UOS_PACKAGED", "1")
    monkeypatch.setenv("MPV_UOS_CLOUDFLARED", str(FAKE))
    root = tmp_path / "app"
    (root / "vendor" / "bin").mkdir(parents=True)
    bundled = root / "vendor" / "bin" / "cloudflared"
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled.chmod(0o755)
    assert find_cloudflared(root) == bundled


def test_vendor_lock_fija_las_tres_plataformas():
    lock = {}
    for line in (ROOT / "vendor.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            lock[k] = v
    for key in ("CLOUDFLARED_SHA256_x86_64_linux", "CLOUDFLARED_SHA256_aarch64_linux",
                "CLOUDFLARED_SHA256_x86_64_windows"):
        assert len(lock[key]) == 64 and all(c in "0123456789abcdef" for c in lock[key]), key
    assert lock["CLOUDFLARED_BASE_URL"].endswith(lock["CLOUDFLARED_VERSION"])


def test_la_descarga_bajo_demanda_verifica_e_instala(tmp_path, monkeypatch):
    if not _linux_amd64():
        pytest.skip("el lock de prueba usa la suma de linux-amd64")
    payload = b"#!/bin/sh\necho cloudflared\n" + b"x" * (1 << 20)
    srv, _ = _asset_server(payload)
    root = tmp_path / "app"
    _write_lock(root, f"http://127.0.0.1:{srv.server_address[1]}", hashlib.sha256(payload).hexdigest())
    monkeypatch.delenv("MPV_UOS_VENDOR_BIN", raising=False)
    monkeypatch.delenv("MPV_UOS_CLOUDFLARED", raising=False)
    seen: list[tuple[int, int]] = []
    try:
        dest = asyncio.run(tunnel_mod.download_cloudflared(
            root, tmp_path / "data", progress=lambda done, total: seen.append((done, total))))
    finally:
        srv.shutdown()
    assert dest == root / "vendor" / "bin" / "cloudflared"
    assert dest.read_bytes() == payload and os.access(dest, os.X_OK)
    assert seen and seen[-1][0] == len(payload)


def test_un_hash_malo_rechaza_y_borra_el_temporal(tmp_path, monkeypatch):
    if not _linux_amd64():
        pytest.skip("el lock de prueba usa la suma de linux-amd64")
    payload = b"no es cloudflared" * 1000
    srv, _ = _asset_server(payload)
    root = tmp_path / "app"
    _write_lock(root, f"http://127.0.0.1:{srv.server_address[1]}", "0" * 64)
    monkeypatch.delenv("MPV_UOS_VENDOR_BIN", raising=False)
    try:
        with pytest.raises(CloudflaredDownloadError, match="SHA-256"):
            asyncio.run(tunnel_mod.download_cloudflared(root, tmp_path / "data"))
    finally:
        srv.shutdown()
    assert not (root / "vendor" / "bin" / "cloudflared").exists()
    assert not (root / "vendor" / "bin" / "cloudflared.part").exists()


def test_la_descarga_se_reanuda_con_range(tmp_path, monkeypatch):
    if not _linux_amd64():
        pytest.skip("el lock de prueba usa la suma de linux-amd64")
    payload = b"y" * (1 << 20)
    srv, handler = _asset_server(payload)
    root = tmp_path / "app"
    _write_lock(root, f"http://127.0.0.1:{srv.server_address[1]}", hashlib.sha256(payload).hexdigest())
    part = root / "vendor" / "bin" / "cloudflared.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(payload[: len(payload) // 2])
    monkeypatch.delenv("MPV_UOS_VENDOR_BIN", raising=False)
    try:
        dest = asyncio.run(tunnel_mod.download_cloudflared(root, tmp_path / "data"))
    finally:
        srv.shutdown()
    assert handler.ranges == [f"bytes={len(payload) // 2}-"]
    assert dest.read_bytes() == payload


def test_sin_red_lo_dice_y_no_deja_basura(tmp_path, monkeypatch):
    if not _linux_amd64():
        pytest.skip("el lock de prueba usa la suma de linux-amd64")
    root = tmp_path / "app"
    _write_lock(root, "http://127.0.0.1:1", "0" * 64)
    monkeypatch.delenv("MPV_UOS_VENDOR_BIN", raising=False)
    with pytest.raises(CloudflaredDownloadError, match="no se pudo descargar"):
        asyncio.run(tunnel_mod.download_cloudflared(root, tmp_path / "data"))
    assert not (root / "vendor" / "bin" / "cloudflared").exists()


def test_si_no_hay_asset_para_esta_plataforma_lo_dice(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel_mod, "platform_asset", lambda root: None)
    with pytest.raises(CloudflaredDownloadError, match="no hay un cloudflared fijado"):
        asyncio.run(tunnel_mod.download_cloudflared(tmp_path, tmp_path / "data"))


def test_si_cloudflared_esta_desactivado_no_descarga(tmp_path, monkeypatch):
    monkeypatch.setenv("MPV_UOS_CLOUDFLARED", "0")
    with pytest.raises(CloudflaredDownloadError, match="desactivado"):
        asyncio.run(tunnel_mod.download_cloudflared(tmp_path, tmp_path / "data"))


def test_el_pid_huerfano_se_barre(tmp_path):
    pid_file = tmp_path / "cloudflared.pid"
    fake_pid = tmp_path / "fake.pid"
    env = {**os.environ, "FAKE_CF_PIDFILE": str(fake_pid)}
    proc = subprocess.Popen([sys.executable, str(FAKE)], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not fake_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)                  # wait for exec: /proc/<pid>/cmdline must already say cloudflared
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        assert tunnel_mod.kill_stale(pid_file) == proc.pid
        proc.wait(timeout=5)          # this test is the parent: reap it so it is not a zombie
        assert proc.returncode is not None
        assert not pid_file.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_no_se_mata_un_pid_que_no_es_cloudflared(tmp_path):
    pid_file = tmp_path / "cloudflared.pid"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        assert tunnel_mod.kill_stale(pid_file) is None
        assert proc.poll() is None
        assert not pid_file.exists()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG es de Linux")
def test_kill_9_del_padre_mata_cloudflared(tmp_path):
    """The real promise of F1: SIGKILL to the daemon leaves no cloudflared behind (PDEATHSIG)."""
    cloudflared_pid_file = tmp_path / "cloudflared.pid"
    script = tmp_path / "parent.py"
    script.write_text(
        "import asyncio, sys, time\n"
        "from pathlib import Path\n"
        "from mpvd.share.tunnel import CloudflaredTunnel\n"
        "pid_file = Path(sys.argv[2])\n"
        "asyncio.run(CloudflaredTunnel(Path(sys.argv[1]), pid_file=pid_file).start(0))\n"
        "print(pid_file.read_text(), flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(script), str(FAKE), str(cloudflared_pid_file)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT)})
    try:
        pid = int(parent.stdout.readline().strip())
        assert alive(pid)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        assert wait_gone(pid), "cloudflared sobrevivió al kill -9 de su padre"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if cloudflared_pid_file.exists():
            with contextlib.suppress(Exception):
                leftover = int(cloudflared_pid_file.read_text())
                if alive(leftover):
                    os.kill(leftover, signal.SIGKILL)


def test_sin_binario_se_dice_y_se_puede_reintentar_sin_cerrar_la_sala(daemon_env, media_dir):
    """F1 · a download that cannot happen leaves the LAN room working and a «retry» that does not need a restart."""
    daemon_env.extra_env.update({
        "MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PUBLIC_HOST": "127.0.0.1", "MPVD_SHARE_PORT": "0",
        "MPV_UOS_CLOUDFLARED": "0", "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
    })
    from tests.conftest import start_mpv

    h = start_mpv(daemon_env.runtime_dir,
                  ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5",
                   "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

        res = daemon_env.call("share.create", {"internet": True})
        assert res["url"].startswith("http://127.0.0.1:")     # la sala sirve ya en la red local
        st = esperar(daemon_env, lambda s: s["tunnel_state"] == "failed", timeout=20)
        assert "desactivado" in st["tunnel_error"] and st["public_url"] is None
        assert daemon_env.call("share.link")["url"].startswith("http://127.0.0.1:")

        retry = daemon_env.call("share.tunnel_retry")          # no cierra la sala ni reinicia la app
        assert retry["tunnel_state"] in ("downloading", "failed")
        daemon_env.call("share.close")
    finally:
        h.stop()
