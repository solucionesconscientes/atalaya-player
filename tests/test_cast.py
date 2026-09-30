"""H27 · «Enviar a la tele» (mpvd/cast): SSDP discovery and UPnP description parsing, SOAP envelopes, and the whole
path against a fake DLNA renderer on loopback (SSDP answered over unicast, description + AVTransport over HTTP; on
Play it fetches the media like a TV: byte ranges for a file, an MPEG-TS relay for what the TV cannot decode)."""

from __future__ import annotations

import asyncio
import http.server
import re
import socket
import subprocess
import threading
from pathlib import Path
from xml.sax.saxutils import unescape

import pytest

from mpvd.cast import dlna
from mpvd.cast.service import Media, relay_argv, tv_can_play
from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.server import MpvdServer

DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>[TV] Salón</friendlyName>
    <manufacturer>Samsung Electronics</manufacturer>
    <modelName>UE43</modelName>
    <UDN>uuid:fake-tv-1</UDN>
    <serviceList>
      <service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <controlURL>/upnp/control/RenderingControl1</controlURL></service>
      <service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>upnp/control/AVTransport1</controlURL></service>
    </serviceList>
  </device>
</root>"""


def test_parse_description_and_ssdp():
    r = dlna.parse_description(DESCRIPTION, "http://192.168.1.50:9197/dmr")
    assert r is not None and r.name == "[TV] Salón" and r.id == "uuid:fake-tv-1"
    assert r.avtransport == "http://192.168.1.50:9197/upnp/control/AVTransport1"
    assert r.rendering == "http://192.168.1.50:9197/upnp/control/RenderingControl1"
    with_base = DESCRIPTION.replace("<specVersion>", "<URLBase>http://10.0.0.2:1234/</URLBase><specVersion>")
    assert dlna.parse_description(with_base, "http://x/").avtransport == "http://10.0.0.2:1234/upnp/control/AVTransport1"
    no_avt = DESCRIPTION.replace("AVTransport:1", "ConnectionManager:1")
    assert dlna.parse_description(no_avt, "http://x/") is None
    h = dlna.parse_ssdp_response(b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLOCATION: http://1.2.3.4/d.xml\r\n"
                                 b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n")
    assert h["location"] == "http://1.2.3.4/d.xml"
    assert dlna.parse_ssdp_response(b"M-SEARCH * HTTP/1.1\r\n\r\n") == {}
    msg = dlna.search_message().decode()
    assert msg.startswith("M-SEARCH * HTTP/1.1\r\n") and 'MAN: "ssdp:discover"' in msg and msg.endswith("\r\n\r\n")


def test_soap_and_didl():
    env = dlna.soap_envelope(dlna.AVT, "Seek", [("InstanceID", "0"), ("Unit", "REL_TIME"), ("Target", dlna.hms(3725))])
    assert b"<u:Seek xmlns:u=\"urn:schemas-upnp-org:service:AVTransport:1\">" in env and b"<Target>1:02:05</Target>" in env
    assert dlna.parse_hms("1:02:05") == 3725 and dlna.parse_hms("00:00:07.500") == 7.5 and dlna.parse_hms("NOT_IMPL") is None
    d = dlna.didl("http://h/c/t/a&b.mp4", "Año & <día>", "video/mp4", True, 60)
    assert "DLNA.ORG_OP=01" in d and "a&amp;b.mp4" in d and "Año &amp; &lt;día&gt;" in d and 'duration="0:01:00.000"' in d
    assert "object.item.audioItem" in dlna.didl("u", "t", "audio/mpeg", False)
    resp = (b'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            b'<u:GetPositionInfoResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1"><Track>1</Track>'
            b"<RelTime>0:00:12</RelTime><TrackDuration>0:00:30</TrackDuration></u:GetPositionInfoResponse></s:Body></s:Envelope>")
    assert dlna.parse_soap_response(resp)["RelTime"] == "0:00:12"


def test_tv_can_play_and_relay_argv(media_dir):
    h264 = {"format": {"format_name": "matroska,webm"}, "streams": [{"codec_type": "video", "codec_name": "h264"},
                                                                    {"codec_type": "audio", "codec_name": "aac"}]}
    assert tv_can_play(h264) == (True, True)
    opus = {"format": {"format_name": "matroska,webm"}, "streams": [{"codec_type": "video", "codec_name": "h264"},
                                                                    {"codec_type": "audio", "codec_name": "opus"}]}
    assert tv_can_play(opus) == (False, True)
    vp9 = {"format": {"format_name": "matroska,webm"}, "streams": [{"codec_type": "video", "codec_name": "vp9"}]}
    assert tv_can_play(vp9) == (False, False)
    m = Media("t" * 20, "x", "video/mp2t", False, relay=[{"url": "https://v/1", "headers": {"Referer": "https://w/"}},
                                                          {"url": "https://a/2", "headers": {}}])
    argv = relay_argv(m, 30.0, "ffmpeg")
    assert argv.count("-ss") == 2 and argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-map") + 1] == "0:v:0" and "mpegts" in argv and "copy" in argv
    m.audio_only = True
    argv = relay_argv(m, 0, "ffmpeg")
    assert "-ss" not in argv and argv[-3:] == ["-f", "mp3", "pipe:1"] and "1:a:0" in argv


class FakeTV:
    """A DLNA renderer: SSDP answers over unicast UDP, description and SOAP over HTTP; Play fetches the media."""

    def __init__(self, tmp: Path):
        self.actions: list[tuple[str, dict[str, str]]] = []
        self.uri = ""
        self.meta = ""
        self.state = "NO_MEDIA_PRESENT"
        self.fetched: list[tuple[int, str, bytes]] = []      # (status, content-type, first bytes)
        self.range_status = 0
        self.tmp = tmp
        tv = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: ANN002
                pass

            def do_GET(self):  # noqa: N802
                body = DESCRIPTION.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                data = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                action = self.headers.get("SOAPACTION", "").strip('"').split("#")[-1]
                args = dict(re.findall(r"<(\w+)>(.*?)</\1>", data.split(f"<u:{action}", 1)[1], re.S))
                tv.actions.append((action, args))
                out = ""
                if action == "SetAVTransportURI":
                    tv.uri, tv.meta = unescape(args["CurrentURI"]), unescape(args["CurrentURIMetaData"])
                    tv.state = "STOPPED"
                elif action == "Play":
                    tv.state = "PLAYING"
                    threading.Thread(target=tv.fetch, daemon=True).start()
                elif action == "Pause":
                    tv.state = "PAUSED_PLAYBACK"
                elif action == "Stop":
                    tv.state = "STOPPED"
                elif action == "GetPositionInfo":
                    out = "<RelTime>0:00:04</RelTime><TrackDuration>0:00:30</TrackDuration>"
                elif action == "GetTransportInfo":
                    out = f"<CurrentTransportState>{tv.state}</CurrentTransportState>"
                body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                        f'<u:{action}Response xmlns:u="urn:x">{out}</u:{action}Response></s:Body></s:Envelope>').encode()
                self.send_response(200)
                self.send_header("Content-Type", 'text/xml; charset="utf-8"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.location = f"http://127.0.0.1:{self.http.server_address[1]}/dmr.xml"
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(0.2)
        self.ssdp_addr = f"127.0.0.1:{self.udp.getsockname()[1]}"
        self._stop = threading.Event()
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        threading.Thread(target=self._ssdp, daemon=True).start()

    def _ssdp(self):
        while not self._stop.is_set():
            try:
                data, addr = self.udp.recvfrom(2048)
            except OSError:
                continue
            if b"M-SEARCH" in data and b"MediaRenderer" in data:
                self.udp.sendto(("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\n"
                                 f"LOCATION: {self.location}\r\nST: {dlna.ST_RENDERER}\r\n"
                                 "USN: uuid:fake-tv-1::urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode(), addr)

    def fetch(self):
        import urllib.request
        if not self.uri.startswith("http://127.0.0.1") and "://" in self.uri and "cast" not in self.uri:
            return
        try:
            req = urllib.request.Request(self.uri.replace(self.uri.split("/")[2].split(":")[0], "127.0.0.1", 1),
                                         headers={"Range": "bytes=100-"})
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
                self.fetched.append((r.status, r.headers.get("Content-Type", ""), r.read(64 * 1024)))
        except Exception as exc:  # noqa: BLE001
            self.fetched.append((0, repr(exc), b""))

    def last(self) -> tuple[str, dict[str, str]]:
        return [a for a in self.actions if not a[0].startswith("Get")][-1]

    def close(self):
        self._stop.set()
        self.http.shutdown()
        self.udp.close()


@pytest.fixture
def tv(tmp_path, monkeypatch):
    t = FakeTV(tmp_path)
    monkeypatch.setenv("MPV_UOS_SSDP_ADDR", t.ssdp_addr)
    monkeypatch.setenv("MPV_UOS_CAST_PORT", "0")
    yield t
    t.close()


def run(tmp_path: Path, fn):
    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=2)
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


async def wait_for(pred, timeout=20.0):
    for _ in range(int(timeout * 10)):
        if pred():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not met")


def test_cast_file_and_relay(tv, tmp_path, media_dir):
    # an H.264/AAC file the TV plays as is, and an H.264/Opus one it cannot (relay: video copied, AAC)
    mp4 = tmp_path / "tv.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media_dir / "video30.mkv"), "-t", "5", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(mp4)], check=True)
    opus = tmp_path / "opus.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media_dir / "video30.mkv"), "-c:v", "copy", "-c:a",
                    "libopus", "-b:a", "64k", str(opus)], check=True)

    async def fn(server, c):
        found = await c.call("cast.discover", {"timeout": 1.5})
        assert [d["name"] for d in found["devices"]] == ["[TV] Salón"]
        dev = found["devices"][0]["id"]

        st = await c.call("cast.play", {"device": dev, "path": str(mp4), "start": 0})
        assert st["casting"] and st["mode"] == "file" and st["state"] == "PLAYING" and st["position"] == 4
        await wait_for(lambda: tv.fetched)
        status, ctype, data = tv.fetched[-1]
        assert status == 206 and ctype == "video/mp4" and data == mp4.read_bytes()[100:100 + len(data)]
        assert "DLNA.ORG_OP=01" in tv.meta and "<dc:title>tv</dc:title>" in tv.meta
        assert [a for a, _ in tv.actions][:3] == ["Stop", "SetAVTransportURI", "Play"]

        await c.call("cast.control", {"action": "pause"})
        assert tv.state == "PAUSED_PLAYBACK"
        await c.call("cast.control", {"action": "seek", "value": 12})
        assert tv.last() == ("Seek", {"InstanceID": "0", "Unit": "REL_TIME", "Target": "0:00:12"})
        await c.call("cast.control", {"action": "volume", "value": 30})
        assert tv.last()[0] == "SetVolume" and tv.last()[1]["DesiredVolume"] == "30"

        # a token only serves the current item
        old = tv.uri
        n = len(tv.fetched)
        st = await c.call("cast.play", {"device": dev, "path": str(opus), "start": 10})
        assert st["mode"] == "relay" and st["position"] == 14          # relay clock + its offset
        await wait_for(lambda: len(tv.fetched) > n)
        status, ctype, data = tv.fetched[-1]
        assert status == 200 and ctype == "video/mp2t" and data[:1] == b"\x47"   # MPEG-TS sync byte
        assert "DLNA.ORG_OP=00" in tv.meta
        import urllib.error
        import urllib.request
        with pytest.raises(urllib.error.HTTPError):
            await asyncio.to_thread(urllib.request.urlopen,  # noqa: S310 - the server runs in this loop
                                    old.replace(old.split("/")[2].split(":")[0], "127.0.0.1", 1), timeout=5)

        st = await c.call("cast.stop")
        assert not st["casting"] and server.cast.http is None and not server.cast._relays
        assert tv.state == "STOPPED"
        with pytest.raises(Exception, match="no se está enviando"):
            await c.call("cast.control", {"action": "pause"})

    run(tmp_path, fn)


def test_cast_errors(tv, tmp_path):
    async def fn(server, c):
        with pytest.raises(Exception, match="no encuentro esa tele"):
            await c.call("cast.play", {"device": "uuid:nope", "path": "/x.mp4"})
        await c.call("cast.discover", {"timeout": 1})
        with pytest.raises(Exception, match="no existe"):
            await c.call("cast.play", {"device": "uuid:fake-tv-1", "path": str(tmp_path / "none.mp4")})
        with pytest.raises(Exception, match="mode"):
            await c.call("cast.play", {"device": "uuid:fake-tv-1", "path": "/x", "mode": "zzz"})

    run(tmp_path, fn)


def test_mu_cast_send_and_come_back(tv, daemon_env, media_dir):
    from tests.conftest import start_mpv

    daemon_env.extra_env.update(MPV_UOS_SSDP_ADDR=tv.ssdp_addr, MPV_UOS_CAST_PORT="0")
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,"
                                           "mu-core-rpc_timeout=5,mu-cast-discover_seconds=1,mu-cast-poll_seconds=0.5",
                                           "--keep-open=yes", "--pause=no"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 1, timeout=20)
        h.command("script-binding", "mu_cast/cast-menu")
        st = h.wait_property("user-data/mu/cast", lambda v: bool(v) and v.get("devices"), timeout=20)
        assert st["devices"][0]["name"] == "[TV] Salón"
        nav = h.wait_property("user-data/mu/nav", lambda v: bool(v) and v.get("title", "").endswith("Enviar a la tele"),
                              timeout=10)
        assert nav["title"].startswith("MPV-UOS › ")

        base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}

        def activate(value):
            h.command("script-message-to", "mu_cast", "mu-cast-event",
                      __import__("json").dumps({**base, "type": "activate", "index": 1, "value": value}))

        activate({"device": "uuid:fake-tv-1"})
        st = h.wait_property("user-data/mu/cast", lambda v: bool(v) and v.get("casting"), timeout=30)
        assert h.get("pause") is True and st["status"]["mode"] == "file"
        assert any(i["title"] == "Seguir viendo aquí" for i in st["items"])
        h.wait_property("user-data/mu/cast", lambda v: any(i["title"] == "Seguir viendo aquí" for i in v["items"]),
                        timeout=10)
        activate({"action": "pause"})
        h.wait_property("user-data/mu/cast", lambda v: (v.get("status") or {}).get("state") == "PAUSED_PLAYBACK",
                        timeout=10)
        activate({"action": "here"})
        h.wait_property("user-data/mu/cast", lambda v: bool(v) and not v.get("casting"), timeout=15)
        h.wait_property("pause", lambda v: v is False, timeout=10)
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= 4, timeout=10)   # the TV's 0:04
        assert tv.state == "STOPPED"
    finally:
        h.stop()
