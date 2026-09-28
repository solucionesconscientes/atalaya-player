"""Radio Browser client against a local JSON server (no internet)."""

import http.server
import json
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from mpvd.iptv.radiobrowser import RadioBrowser
from mpvd.net import FetchError, HttpCache

STATION = {
    "stationuuid": "9617a958-0601-11e8-ae97-52543be04c81", "name": "Radio 3", "url": "http://r3.example/live",
    "url_resolved": "http://r3.example/live.mp3", "homepage": "https://www.rtve.es/radio/radio3/",
    "favicon": "https://x/r3.png", "tags": "public radio,culture,music", "country": "Spain", "countrycode": "ES",
    "language": "spanish", "votes": 1234, "codec": "MP3", "bitrate": 192, "hls": 0, "lastcheckok": 1,
}


class Log:
    paths: list[str] = []
    fail_first = False


def make_handler(log):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            log.paths.append(self.path)
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if log.fail_first:
                log.fail_first = False
                self.send_response(503)
                self.end_headers()
                return
            if u.path == "/json/countries":
                body = [{"name": "Spain", "iso_3166_1": "ES", "stationcount": 900},
                        {"name": "Germany", "iso_3166_1": "DE", "stationcount": 3000}, {"name": "?", "iso_3166_1": "", "stationcount": 1}]
            elif u.path == "/json/tags":
                body = [{"name": "pop", "stationcount": 5000}, {"name": "", "stationcount": 1}]
            elif u.path == "/json/stations/bycountrycodeexact/ES":
                body = [STATION, {**STATION, "name": "Sin URL", "url": "", "url_resolved": ""}]
            elif u.path == "/json/stations/search":
                assert q["name"] == ["radio 3"] and q.get("countrycode") == ["ES"] and q["hidebroken"] == ["true"]
                body = [STATION]
            elif u.path == "/json/stations/topvote/2":
                body = [STATION, {**STATION, "name": "Otra", "url": "http://o.example/x", "url_resolved": ""}]
            elif u.path.startswith("/json/url/"):
                body = {"ok": "true"}
            else:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return H


@pytest.fixture
def api(tmp_path):
    log = Log()
    log.paths = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), make_handler(log))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield RadioBrowser(HttpCache(tmp_path / "http", user_agent="MPV-UOS-test"), base_url=f"http://127.0.0.1:{httpd.server_port}"), log
    finally:
        httpd.shutdown()


def test_directory_and_stations(api):
    rb, log = api
    assert rb.countries() == [{"name": "Germany", "code": "de", "count": 3000}, {"name": "Spain", "code": "es", "count": 900}]
    assert rb.tags(10) == [{"name": "pop", "count": 5000}]
    es = rb.by_country("es")
    assert [c.name for c in es] == ["Radio 3"]  # station without URL dropped
    ch = es[0]
    assert ch.kind == "radio" and ch.source == "radio_browser" and ch.url == "http://r3.example/live.mp3"
    assert ch.country == "es" and ch.group == "Spain" and ch.category == "public radio" and ch.logo.endswith("r3.png")
    assert ch.extra["stationuuid"] == STATION["stationuuid"] and ch.extra["bitrate"] == "192"
    assert rb.stations[ch.id] == ch
    assert [c.name for c in rb.search("radio 3", country="es")] == ["Radio 3"]
    assert [c.name for c in rb.top(2)] == ["Radio 3", "Otra"]
    rb.click(ch.extra["stationuuid"])
    assert any(p.startswith("/json/url/9617a958") for p in log.paths)
    rb.by_country("es")  # cached: no new request
    assert sum(p.startswith("/json/stations/bycountrycodeexact/ES") for p in log.paths) == 1


def test_server_failure_and_rotation(api):
    rb, log = api
    log.fail_first = True
    rb._servers.append("http://127.0.0.1:1")  # a dead second server
    with pytest.raises(FetchError):
        rb.countries()  # first server 503, second unreachable -> error
    assert rb.countries()[0]["code"] == "de"  # rotation brought the good server back
