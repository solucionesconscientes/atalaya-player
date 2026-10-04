"""Scheduled recordings (H21): manual time entry, ffmpeg arguments with the channel's headers, a real recording of a
local live HLS (served only to a browser User-Agent), cancel/remove, persistence, «perdida» after a restart and an
interrupted recording that goes on after mpvd comes back."""

from __future__ import annotations

import asyncio
import datetime as dt
import http.server
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.iptv.schedule import ffmpeg_args, parse_when, safe_name
from mpvd.iptv.sources import Source
from mpvd.server import MpvdServer


def local(y, mo, d, h, mi) -> float:
    return dt.datetime(y, mo, d, h, mi).timestamp()


NOW = local(2026, 9, 30, 20, 0)


@pytest.mark.parametrize("text,start,stop", [
    ("21:30 22:15", (30, 21, 30), (30, 22, 15)),
    ("21:30-22:15", (30, 21, 30), (30, 22, 15)),
    ("21:30 a 22:15", (30, 21, 30), (30, 22, 15)),
    ("21.30 90", (30, 21, 30), (30, 23, 0)),
    ("21:30 1h30", (30, 21, 30), (30, 23, 0)),
    ("21h 45m", (30, 21, 0), (30, 21, 45)),
    ("21:30", (30, 21, 30), (30, 22, 30)),  # no end: one hour
    ("19:00 20:00", (1, 19, 0), (1, 20, 0)),  # already over today: tomorrow
    ("19:30 21:00", (30, 19, 30), (30, 21, 0)),  # on now: today (records what is left)
    ("23:30 00:30", (30, 23, 30), (1, 0, 30)),  # past midnight
    ("mañana 9:00 30", (1, 9, 0), (1, 9, 30)),
    ("Mañana 21:30 22:00", (1, 21, 30), (1, 22, 0)),
])
def test_parse_when(text, start, stop):
    def ts(t):
        d, h, mi = t
        return local(2026, 9 if d == 30 else 10, d, h, mi)

    got = parse_when(text, NOW)
    assert "error" not in got, got
    assert (got["start"], got["stop"]) == (ts(start), ts(stop))
    assert got["label"]


def test_parse_when_now_and_errors():
    got = parse_when("ahora 30", NOW)
    assert got["start"] == NOW and got["stop"] == NOW + 1800 and got["now"] is True
    assert parse_when("21:30 22:15", NOW)["label"] == "hoy 21:30–22:15 (45 min)"
    assert parse_when("mañana 9:00 2h", NOW)["label"] == "mañana 09:00–11:00 (2 h)"
    for bad in ("", "cena", "21:30 luego", "25:00 26:00", "21:30 22:00 23:00", "ahora 13h"):
        assert "error" in parse_when(bad, NOW), bad


@pytest.mark.parametrize(("texto", "dias"), [
    ("mañana 9:00 30", 1), ("tomorrow 9:00 30", 1), ("demain 9:00 30", 1),
    ("hoy 21:30 30", 0), ("today 21:30 30", 0), ("aujourd’hui 21:30 30", 0), ("aujourd'hui 21:30 30", 0),
    ("pasado mañana 9:00 30", 2), ("après-demain 9:00 30", 2), ("apres-demain 9:00 30", 2),
])
def test_las_palabras_de_dia_valen_en_los_tres_idiomas(texto, dias):
    """H49/G7 · quien escribe la hora está en el idioma del reproductor. Las castellanas siguen valiendo siempre."""
    got = parse_when(texto, NOW)
    assert "error" not in got, (texto, got)
    assert dt.datetime.fromtimestamp(got["start"]).date() == dt.date(2026, 9, 30) + dt.timedelta(days=dias)


@pytest.mark.parametrize("texto", ["21:30 a 22:15", "21:30 to 22:15", "de 21:30 à 22:15", "21:30 hasta 22:15",
                                   "21:30 until 22:15"])
def test_los_conectores_tambien(texto):
    got = parse_when(texto, NOW)
    assert "error" not in got, (texto, got)
    assert (got["start"], got["stop"]) == (local(2026, 9, 30, 21, 30), local(2026, 9, 30, 22, 15))


@pytest.mark.parametrize("texto", ["ahora 30", "now 30", "maintenant 30"])
def test_ahora_en_los_tres_idiomas(texto):
    got = parse_when(texto, NOW)
    assert got["start"] == NOW and got["stop"] == NOW + 1800 and got["now"] is True


def test_la_ficha_de_la_programacion_sale_en_el_idioma_del_reproductor(monkeypatch):
    """El «hoy 21:30–22:15 (45 min)» que se ve en el menú va en el idioma del reproductor, no en castellano."""
    for entorno, hoy in (("es_ES.UTF-8", "hoy"), ("en_US.UTF-8", "today"), ("fr_FR.UTF-8", "aujourd’hui")):
        monkeypatch.setenv("LC_ALL", entorno)
        monkeypatch.setenv("LANG", entorno)
        assert parse_when("21:30 22:15", NOW)["label"].startswith(hoy + " 21:30"), entorno


def test_ffmpeg_args_send_the_channel_headers():
    ch = {"url": "https://tv.example/live.m3u8", "kind": "tv", "hls": True,
          "headers": {"User-Agent": "Mozilla/5.0 X", "Referer": "https://web.example/", "Origin": "https://web.example"}}
    cmd = ffmpeg_args(ch, Path("/tmp/out.mkv"), 60)
    joined = " ".join(cmd)
    assert cmd[cmd.index("-user_agent") + 1] == "Mozilla/5.0 X"
    assert cmd[cmd.index("-referer") + 1] == "https://web.example/"
    assert cmd[cmd.index("-headers") + 1] == "Origin: https://web.example\r\n"
    assert "-http_persistent 0" in joined and "-seg_max_retry 3" in joined
    assert cmd.index("-user_agent") < cmd.index("-i") < cmd.index("-c")
    assert cmd[cmd.index("-c") + 1] == "copy" and "-sn" in cmd and "-vn" not in cmd and cmd[-1] == "/tmp/out.mkv"
    radio = ffmpeg_args({"url": "http://r.example/s.mp3", "kind": "radio", "headers": {}}, Path("/tmp/r.mka"), 5)
    assert "-vn" in radio and "-http_persistent" not in radio and "-user_agent" not in radio
    assert safe_name('La 1: "Telediario" 21/09?') == "La 1 Telediario 21 09"


# -- a real recording ---------------------------------------------------------------------------------------------


class LiveHls:
    """A live HLS (1 s segments, sliding window) written in real time by ffmpeg and served over HTTP only to a
    browser User-Agent with our Referer (like CDNs that refuse "Lavf"/"libmpv")."""

    def __init__(self, folder: Path):
        self.dir = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.requests: list[dict[str, str]] = []
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi",
             "-i", "sine=frequency=440:sample_rate=48000", "-t", "300", "-c:v", "libx264", "-preset", "ultrafast",
             "-tune", "zerolatency", "-g", "25", "-c:a", "aac", "-f", "hls", "-hls_time", "1", "-hls_list_size", "6",
             "-hls_flags", "delete_segments", str(folder / "live.m3u8")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        live = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                name = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
                ua, ref = self.headers.get("User-Agent", ""), self.headers.get("Referer", "")
                live.requests.append({"name": name, "ua": ua, "ref": ref})
                f = live.dir / name
                if "Mozilla/5.0" not in ua or ref != "https://web.example/":
                    self.send_response(403)
                    self.end_headers()
                    return
                if not f.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        self.httpd = Server(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/hls/live.m3u8"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            pl = folder / "live.m3u8"
            if pl.exists() and pl.read_text().count("#EXTINF") >= 3:
                break
            time.sleep(0.2)

    def close(self) -> None:
        self.httpd.shutdown()
        self.proc.kill()
        self.proc.wait(timeout=10)


@pytest.fixture
def live(tmp_path):
    hls = LiveHls(tmp_path / "hls")
    try:
        yield hls
    finally:
        hls.close()


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def make_server(tmp_path: Path, list_url: str) -> MpvdServer:
    return MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                               idle_timeout=0, workers=2),
                      iptv_sources=[Source("tdt_tv", "España TV", list_url, "tv", "es", country="es")])


def serve_list(body: bytes):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


async def wait_status(c: MpvdClient, rid: str, statuses: tuple[str, ...], timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        items = {i["id"]: i for i in (await c.call("iptv.schedule.list"))["items"]}
        if rid in items and items[rid]["status"] in statuses:
            return items[rid]
        if time.monotonic() > deadline:
            raise AssertionError(f"{rid}: {items.get(rid)} never reached {statuses}")
        await asyncio.sleep(0.2)


def test_record_cancel_persist_and_missed(tmp_path, live):
    m3u = (f'#EXTM3U\n#EXTINF:-1 tvg-id="Live.TV" group-title="Pruebas",Directo Prueba\n'
           f"#EXTVLCOPT:http-referrer=https://web.example/\n{live.url}\n").encode()
    lst = serve_list(m3u)
    list_url = f"http://127.0.0.1:{lst.server_port}/tv.m3u8"
    rec_dir = tmp_path / "Grabaciones"

    async def first_run():
        server = make_server(tmp_path, list_url)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                chans = (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True}))["items"]
                cid = chans[0]["id"]
                now = time.time()
                rec = await c.call("iptv.schedule.add", {"channel": cid, "start": now + 2, "stop": now + 6,
                                                         "title": "Informativo", "dir": str(rec_dir)})
                assert rec["status"] == "scheduled" and rec["status_label"] == "programada"
                assert server.schedule.busy()  # mpvd must not exit for inactivity now
                same = await c.call("iptv.schedule.add", {"channel": cid, "start": now + 2, "stop": now + 6,
                                                          "dir": str(rec_dir)})
                assert same["id"] == rec["id"]  # the same programme twice is one recording
                t0 = time.monotonic()
                running = await wait_status(c, rec["id"], ("recording",), 10)
                started_after = time.monotonic() - t0
                done = await wait_status(c, rec["id"], ("done", "failed"), 30)
                ended_after = time.monotonic() - t0

                # cancel: a pending one is dropped, a running one is stopped and keeps what it recorded
                future = await c.call("iptv.schedule.add", {"channel": cid, "start": now + 3600, "stop": now + 7200,
                                                            "dir": str(rec_dir)})
                cancelled = await c.call("iptv.schedule.cancel", {"id": future["id"]})
                now = time.time()
                long = await c.call("iptv.schedule.add", {"channel": cid, "start": now, "stop": now + 120,
                                                          "title": "Largo", "dir": str(rec_dir)})
                await wait_status(c, long["id"], ("recording",), 10)
                await asyncio.sleep(3)
                await c.call("iptv.schedule.cancel", {"id": long["id"]})
                stopped = await wait_status(c, long["id"], ("done", "cancelled", "failed"), 20)
                removed = await c.call("iptv.schedule.remove", {"id": future["id"]})
                ids_after_remove = [i["id"] for i in (await c.call("iptv.schedule.list"))["items"]]

                # for the restart: one that will be over by then, one far ahead, one interrupted mid-recording
                now = time.time()
                gone = await c.call("iptv.schedule.add", {"channel": cid, "start": now + 5, "stop": now + 6,
                                                          "dir": str(rec_dir)})
                later = await c.call("iptv.schedule.add", {"channel": cid, "start": now + 86400, "stop": now + 90000,
                                                           "dir": str(rec_dir)})
                cut = await c.call("iptv.schedule.add", {"channel": cid, "start": now, "stop": now + 16,
                                                         "title": "Cortado", "dir": str(rec_dir)})
                await wait_status(c, cut["id"], ("recording",), 10)
                await asyncio.sleep(3)
                return (running, started_after, done, ended_after, cancelled, stopped, removed, ids_after_remove,
                        gone, later, cut)
        finally:
            await server.stop()  # stops the running ffmpeg properly; «Cortado» stays «recording»

    try:
        (running, started_after, done, ended_after, cancelled, stopped, removed, ids_after_remove, gone, later,
         cut) = asyncio.run(first_run())
        saved = json.loads((tmp_path / "data" / "iptv-schedule.json").read_text(encoding="utf-8"))
        assert {r["id"]: r["status"] for r in saved}[cut["id"]] == "recording"

        assert 1.0 <= started_after <= 4.5, started_after
        assert done["status"] == "done", done
        assert 3.0 <= ended_after <= 12, ended_after
        f = Path(done["file"])
        assert f.parent == rec_dir and f.suffix == ".mkv" and f.name.startswith("Directo Prueba - Informativo - ")
        info = probe(f)
        kinds = sorted(s["codec_type"] for s in info["streams"])
        assert kinds == ["audio", "video"], info
        assert 2.5 <= float(info["format"]["duration"]) <= 12, info
        # every request carried the browser User-Agent and the list's Referer (else the server answers 403)
        assert live.requests and all("Mozilla/5.0" in r["ua"] and r["ref"] == "https://web.example/"
                                     for r in live.requests), live.requests[:3]
        assert cancelled["status"] == "cancelled"
        assert stopped["status"] == "done" and stopped["message"] == "detenida antes de tiempo", stopped
        assert Path(stopped["file"]).stat().st_size > 0
        assert removed == {"removed": True} and cancelled["id"] not in ids_after_remove

        # the next start: what was due meanwhile is «perdida», the far one waits, the interrupted one goes on
        time.sleep(max(0.0, gone["end"] - time.time() + 0.5))

        async def second_run():
            server = make_server(tmp_path, list_url)
            await server.start()
            try:
                async with MpvdClient(str(server.settings.socket_path)) as c:
                    items = {i["id"]: i for i in (await c.call("iptv.schedule.list"))["items"]}
                    resumed = items.get(cut["id"])
                    final = await wait_status(c, cut["id"], ("done", "failed"), 30)
                    return items, resumed, final
            finally:
                await server.stop()

        items, resumed, final = asyncio.run(second_run())
        assert items[gone["id"]]["status"] == "missed" and items[gone["id"]]["status_label"] == "perdida"
        assert items[later["id"]]["status"] == "scheduled"
        assert resumed["status"] in ("scheduled", "recording")
        assert final["status"] == "done" and len(final["files"]) == 2, final  # before and after the restart
        assert all(Path(p).stat().st_size > 0 for p in final["files"])
    finally:
        lst.shutdown()


def test_dos_canales_a_la_vez_y_una_serie_que_deja_puesta_la_siguiente(tmp_path, live):
    """H67 · las dos preguntas de Ser, de punta a punta y con ffmpeg de verdad.

    (1) «¿Se pueden grabar varios canales al mismo tiempo?» Sí: cada grabación es su propio ffmpeg copiando el
    flujo (`-c copy`, sin recodificar), así que dos a la vez cuestan dos veces casi nada. Aquí se graban dos
    canales en la misma franja y se comprueba que los DOS archivos existen, tienen vídeo y audio y duran.
    (2) «Que si se dice de l-v, cada día, siga así de forma indefinida»: al terminar una franja repetida, la
    siguiente queda puesta sola, el mismo día de la semana que toque y a la misma hora del reloj."""
    m3u = (f'#EXTM3U\n#EXTINF:-1 tvg-id="Live.TV" group-title="Pruebas",Directo Uno\n'
           f"#EXTVLCOPT:http-referrer=https://web.example/\n{live.url}\n"
           f'#EXTINF:-1 tvg-id="Live2.TV" group-title="Pruebas",Directo Dos\n'
           f"#EXTVLCOPT:http-referrer=https://web.example/\n{live.url}?dos\n").encode()
    lst = serve_list(m3u)
    list_url = f"http://127.0.0.1:{lst.server_port}/tv.m3u8"
    rec_dir = tmp_path / "Grabaciones"

    async def go():
        server = make_server(tmp_path, list_url)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                chans = (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True}))["items"]
                assert len(chans) >= 2, chans
                now = time.time()
                a = await c.call("iptv.schedule.add", {"channel": chans[0]["id"], "start": now + 2,
                                                       "stop": now + 7, "dir": str(rec_dir)})
                b = await c.call("iptv.schedule.add", {"channel": chans[1]["id"], "start": now + 2,
                                                       "stop": now + 7, "dir": str(rec_dir)})
                assert "warning" not in a and "warning" not in b, "grabar dos canales a la vez no estorba"
                # los dos graban a la vez de verdad, no uno detrás de otro
                await wait_status(c, a["id"], ("recording",), 15)
                juntos = {i["id"]: i for i in (await c.call("iptv.schedule.list"))["items"]}
                assert juntos[b["id"]]["status"] in ("recording", "scheduled")
                fin_a = await wait_status(c, a["id"], ("done", "failed"), 40)
                fin_b = await wait_status(c, b["id"], ("done", "failed"), 40)

                # y una franja repetida deja puesta la siguiente al acabar
                serie = await c.call("iptv.schedule.add", {"channel": chans[0]["id"], "start": time.time() + 2,
                                                           "stop": time.time() + 5, "dir": str(rec_dir),
                                                           "repeat": "daily"})
                assert serie["repeat"] == "daily" and serie["repeat_label"] == "cada día"
                await wait_status(c, serie["id"], ("done", "failed"), 40)
                lista = (await c.call("iptv.schedule.list"))["items"]
                return fin_a, fin_b, serie, lista
        finally:
            await server.stop()

    try:
        fin_a, fin_b, serie, lista = asyncio.run(go())
    finally:
        lst.shutdown()

    assert fin_a["status"] == "done" and fin_b["status"] == "done", (fin_a, fin_b)
    for fin in (fin_a, fin_b):
        datos = probe(Path(fin["file"]))
        tipos = sorted(s["codec_type"] for s in datos["streams"])
        assert tipos == ["audio", "video"], datos
        assert float(datos["format"]["duration"]) > 1.0, datos
    assert Path(fin_a["file"]) != Path(fin_b["file"]), "cada canal, su archivo"

    siguiente = [i for i in lista if i["status"] == "scheduled" and i["repeat"] == "daily"]
    assert len(siguiente) == 1, siguiente
    assert siguiente[0]["id"] != serie["id"] and siguiente[0]["series"] == serie["series"]
    primera = dt.datetime.fromtimestamp(serie["start"])
    proxima = dt.datetime.fromtimestamp(siguiente[0]["start"])
    assert (proxima.hour, proxima.minute) == (primera.hour, primera.minute), (primera, proxima)
    assert (proxima.date() - primera.date()).days == 1
