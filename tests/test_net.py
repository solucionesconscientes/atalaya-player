"""HttpCache: TTL, conditional requests (304), updates, offline fallback and errors — against a local server."""

import hashlib
import http.server
import threading
import time

import pytest

from mpvd.net import FetchError, HttpCache


class State:
    body = b"version 1\n"
    requests: list[dict] = []
    fail = False


def make_handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_GET(self):
            state.requests.append({k.lower(): v for k, v in self.headers.items()})
            if state.fail:
                self.send_response(500)
                self.end_headers()
                return
            etag = '"' + hashlib.md5(state.body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("ETag", etag)
            self.send_header("Content-Type", "audio/x-mpegurl")
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

    return Handler


@pytest.fixture
def server():
    state = State()
    state.requests = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), make_handler(state))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield state, f"http://127.0.0.1:{httpd.server_port}/list.m3u"
    finally:
        httpd.shutdown()


def test_fresh_cache_conditional_and_update(tmp_path, server):
    state, url = server
    cache = HttpCache(tmp_path / "http", user_agent="MPV-UOS-test")
    r1 = cache.fetch(url, ttl=60)
    assert r1.status == 200 and not r1.from_cache and r1.read_text() == "version 1\n"
    assert r1.content_type == "audio/x-mpegurl" and r1.etag and state.requests[-1]["user-agent"] == "MPV-UOS-test"
    r2 = cache.fetch(url, ttl=60)  # within TTL: no request at all
    assert r2.from_cache and not r2.stale and len(state.requests) == 1
    r3 = cache.fetch(url, ttl=60, force=True)  # conditional -> 304
    assert r3.status == 304 and r3.from_cache and len(state.requests) == 2
    assert state.requests[-1]["if-none-match"] == r1.etag
    assert r3.fetched_at >= r1.fetched_at
    state.body = b"version 2\n"
    r4 = cache.fetch(url, ttl=0)  # TTL expired -> conditional -> 200 with new body
    assert r4.status == 200 and not r4.from_cache and r4.read_text() == "version 2\n"
    assert cache.cached(url).read_text() == "version 2\n"


def test_stale_on_server_error_and_offline(tmp_path, server):
    state, url = server
    cache = HttpCache(tmp_path / "http")
    cache.fetch(url)
    state.fail = True
    r = cache.fetch(url, ttl=0)
    assert r.stale and r.from_cache and r.read_text() == "version 1\n"
    cache.offline = True
    r = cache.fetch(url, ttl=0)
    assert r.stale
    cache.invalidate(url)
    with pytest.raises(FetchError):
        cache.fetch(url)
    cache.offline = False
    with pytest.raises(FetchError):  # 500 and nothing cached
        cache.fetch(url)


def test_unreachable_host_without_cache_raises(tmp_path):
    cache = HttpCache(tmp_path / "http")
    with pytest.raises(FetchError):
        cache.fetch("http://127.0.0.1:1/nothing", timeout=2)


def test_age_and_ttl_boundary(tmp_path, server):
    state, url = server
    cache = HttpCache(tmp_path / "http")
    cache.fetch(url)
    time.sleep(0.05)
    assert cache.fetch(url, ttl=10).from_cache
    assert cache.fetch(url, ttl=0.01).status == 304
