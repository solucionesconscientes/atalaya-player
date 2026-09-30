"""H23 · subscriptions, pure parts: the RSS/Atom parser on real feeds (fixtures recorded on 2026-09-30), URL detection,
the time window / limit / metered planner with an injected clock, and the chain's naming helpers."""

from __future__ import annotations

import datetime as dt
import http.server
import threading
from pathlib import Path

import pytest

from mpvd.subscriptions import detect, rules
from mpvd.subscriptions.chain import ChainConfig, check_template, render_name, safe_component
from mpvd.subscriptions.rss import FeedError, looks_like_feed, parse_date, parse_duration, parse_feed
from mpvd.subscriptions.store import FeedSettings, FeedStore, Subscription

FIX = Path(__file__).parent / "fixtures" / "feeds"


# -- RSS / Atom ---------------------------------------------------------------------------------------------------


def test_rss_npr_real_feed():
    feed = parse_feed((FIX / "npr_news_now.xml").read_bytes())
    assert feed.kind == "rss" and feed.title == "NPR News Now" and feed.language == "en-us"
    eps = feed.newest_first()
    assert len(eps) == 4
    first = eps[0]
    assert first.id == "516383d0-7d3b-4b54-a0ea-56b8dd55e3c5" and first.title == "NPR News: 09-30-2026 7AM EDT"
    assert first.url.startswith("https://prfx.byspotify.com/") and "&awEpisodeId=" in first.url   # &amp; decoded
    assert first.mime == "audio/mpeg" and first.size == 4480566 and first.duration == 280 and first.direct
    assert first.published == dt.datetime(2026, 9, 30, 11, 10, 41, tzinfo=dt.timezone.utc).timestamp()
    # the trailer is from 2023: last despite being a normal item
    assert eps[-1].title == "NPR News: Trailer" and [e.published for e in eps] == sorted(
        [e.published for e in eps], reverse=True)


def test_rss_rtve_trimmed_spanish_feed():
    feed = parse_feed((FIX / "rtve_180_grados.xml").read_bytes())
    assert feed.title == "180 grados" and feed.language == "es" and len(feed.entries) == 3
    e = feed.newest_first()[0]
    assert e.id == "https://www.rtve.es/a/17247161/" and e.url == "https://ztnr.rtve.es/ztnr/17247161.mp3"
    assert e.title.startswith('180 grados - La Casa Azul estrena "En mi cabeza"') and e.duration == 58 * 60 + 33
    assert e.link.startswith("https://www.rtve.es/play/audios/180-grados/")


def test_atom_youtube_channel_feed():
    feed = parse_feed((FIX / "youtube_blender_channel.xml").read_bytes())
    assert feed.kind == "atom" and feed.title == "Blender" and len(feed.entries) == 3
    e = feed.newest_first()[0]
    assert e.id == "SVJiCL3K0j4" and e.url == "https://www.youtube.com/watch?v=SVJiCL3K0j4" and not e.direct
    assert e.title == "The Future of Rendering — BCON26" and e.published is not None


def test_rss_edge_cases():
    doc = b"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <title>T</title>
      <item><title>Media RSS</title><media:content url="https://x.test/a.mp4" type="video/mp4" duration="61"/></item>
      <item><title>Solo enlace</title><link>https://x.test/page</link><pubDate>Mon, 28 Sep 2026 08:00:00</pubDate></item>
      <item><title>Nada</title></item>
    </channel></rss>"""
    feed = parse_feed(doc)
    assert [e.title for e in feed.entries] == ["Media RSS", "Solo enlace"]
    a, b = feed.entries
    assert a.url == "https://x.test/a.mp4" and a.is_video and a.duration == 61 and a.id == a.url
    assert b.url == "https://x.test/page" and not b.direct and b.published is not None
    with pytest.raises(FeedError):
        parse_feed(b"<html><body>no</body></html>")
    with pytest.raises(FeedError):
        parse_feed(b"<rss><channel><item>")
    assert parse_duration("1:02:03") == 3723 and parse_duration("45") == 45 and parse_duration("x") is None
    assert parse_date("2026-09-30T10:00:00Z") == dt.datetime(2026, 9, 30, 10, tzinfo=dt.timezone.utc).timestamp()
    assert parse_date("mañana") is None
    assert looks_like_feed(b'<?xml version="1.0"?>\n<rss version="2.0">') and looks_like_feed(b"", "application/rss+xml")
    assert not looks_like_feed(b"<!DOCTYPE html><html>") and not looks_like_feed(b"ID3\x04\x00")


# -- detection ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("url", "kind", "norm"), [
    ("https://www.youtube.com/@BlenderOfficial", "channel", "https://www.youtube.com/@BlenderOfficial/videos"),
    ("https://youtube.com/@BlenderOfficial/featured", "channel", "https://www.youtube.com/@BlenderOfficial/videos"),
    ("https://www.youtube.com/@x/streams", "channel", "https://www.youtube.com/@x/streams"),
    ("https://www.youtube.com/channel/UCSMOQeBJ2RAnuFungnQOxLg", "channel",
     "https://www.youtube.com/channel/UCSMOQeBJ2RAnuFungnQOxLg/videos"),
    ("https://www.youtube.com/playlist?list=PLa1F2ddGya_-UvuAqHAksYnB0qL9yWDO6&si=x", "playlist",
     "https://www.youtube.com/playlist?list=PLa1F2ddGya_-UvuAqHAksYnB0qL9yWDO6"),
    ("https://www.youtube.com/watch?v=abc", None, "https://www.youtube.com/watch?v=abc"),
    ("https://www.youtube.com/feeds/videos.xml?channel_id=UC1", "rss",
     "https://www.youtube.com/feeds/videos.xml?channel_id=UC1"),
    ("https://feeds.npr.org/500005/podcast.xml", "rss?", "https://feeds.npr.org/500005/podcast.xml"),
    ("https://www.rtve.es/api/programas/22270/audios.rss", "rss?", "https://www.rtve.es/api/programas/22270/audios.rss"),
    ("https://vimeo.com/channels/staffpicks", None, "https://vimeo.com/channels/staffpicks"),
])
def test_classify_url(url, kind, norm):
    assert detect.classify_url(url) == (kind, norm)


def test_classify_rejects_non_http():
    with pytest.raises(ValueError):
        detect.classify_url("file:///etc/passwd")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture
def http_fixtures():
    handler = lambda *a, **kw: _Handler(*a, directory=str(FIX), **kw)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_sniff_reads_only_the_head(http_fixtures):
    ok, ctype = detect.sniff(http_fixtures + "/npr_news_now.xml")
    assert ok and "xml" in ctype
    bogus = FIX / "not-a-feed.txt"
    bogus.write_text("hola\n" * 100, encoding="utf-8")
    try:
        assert detect.sniff(http_fixtures + "/not-a-feed.txt")[0] is False
    finally:
        bogus.unlink()
    with pytest.raises(OSError):
        detect.sniff(http_fixtures + "/missing.xml")


# -- planner: window, limit, metered ------------------------------------------------------------------------------


def at(h: int, m: int = 0, day: int = 30) -> dt.datetime:
    return dt.datetime(2026, 9, day, h, m)


def test_window_parsing_and_membership():
    w = rules.parse_window("de 1:00 a 7:00")
    assert w == (60, 420) and rules.format_window(w) == "de 1:00 a 7:00"
    assert rules.in_window(at(1), w) and rules.in_window(at(6, 59), w)
    assert not rules.in_window(at(7), w) and not rules.in_window(at(0, 59), w) and not rules.in_window(at(13), w)
    night = rules.parse_window("23:30-6")     # crosses midnight
    assert rules.in_window(at(23, 45), night) and rules.in_window(at(2), night) and not rules.in_window(at(12), night)
    assert rules.parse_window("siempre") is None and rules.in_window(at(12), None)
    assert rules.normalize_window("de las 2 a las 8") == "02:00-08:00" and rules.normalize_window("") == ""
    for bad in ("mañana", "25 a 3", "1:75-3"):
        with pytest.raises(rules.WindowError):
            rules.parse_window(bad)


def test_period_start_and_next_open():
    w = (60, 420)
    assert rules.period_start(at(3), w) == at(1)
    assert rules.period_start(at(0, 30), w) == at(1, day=29)      # before today's start: yesterday's window
    assert rules.period_start(at(15), None) == at(0)
    assert rules.next_open(at(12), w) == at(1, day=1).replace(month=10)
    assert rules.next_open(at(0, 30), w) == at(1)
    assert rules.next_open(at(2), w) == at(2)
    night = (23 * 60, 6 * 60)
    assert rules.period_start(at(2), night) == at(23, day=29) and rules.period_start(at(23, 30), night) == at(23)


def test_gate_limit_per_window_and_metered():
    q = rules.Quota()
    w = rules.parse_window("1:00-7:00")
    assert rules.gate(at(12), w, q, 0, 0, False, True) == (False, "fuera de la franja (de 1:00 a 7:00)")
    assert rules.gate(at(2), w, q, 2, 0, False, True) == (True, "")
    q.items = 2
    assert rules.gate(at(3), w, q, 2, 0, False, True) == (False, "límite de la franja: 2 descargas")
    # next night: a new window, the counter starts again
    assert rules.gate(at(1, 30, day=1).replace(month=10), w, q, 2, 0, False, True) == (True, "")
    assert q.items == 0
    q.bytes = 600_000_000
    assert rules.gate(at(2, day=1).replace(month=10), w, q, 0, 500, False, True)[1] == "límite de la franja: 500 MB"
    # metered: paused only when the setting says so; unknown (None) = not metered
    q2 = rules.Quota()
    assert rules.gate(at(12), None, q2, 0, 0, True, True) == (False, "conexión medida: en pausa")
    assert rules.gate(at(12), None, q2, 0, 0, True, False) == (True, "")
    assert rules.gate(at(12), None, q2, 0, 0, None, True) == (True, "")


NMCLI_WIRED = """GENERAL.STATE:100 (connected)
GENERAL.METERED:no (guessed)
IP4.GATEWAY:192.168.1.1
IP6.GATEWAY:

GENERAL.STATE:100 (connected (externally))
GENERAL.METERED:no (guessed)
IP4.GATEWAY:
IP6.GATEWAY:

GENERAL.STATE:20 (unavailable)
GENERAL.METERED:unknown
IP4.GATEWAY:
IP6.GATEWAY:
"""  # verbatim from this laptop (nmcli 1.54.3, LC_ALL=C), 2026-09-30

NMCLI_PHONE = """GENERAL.STATE:100 (connected)
GENERAL.METERED:yes
IP4.GATEWAY:192.168.43.1
IP6.GATEWAY:

GENERAL.STATE:30 (disconnected)
GENERAL.METERED:unknown
IP4.GATEWAY:
IP6.GATEWAY:
"""


def test_nmcli_parsing_and_override(monkeypatch):
    assert rules.parse_nmcli(NMCLI_WIRED) is False
    assert rules.parse_nmcli(NMCLI_PHONE) is True
    assert rules.parse_nmcli(NMCLI_PHONE.replace("yes", "yes (guessed)")) is True
    assert rules.parse_nmcli("GENERAL.STATE:20 (unavailable)\nGENERAL.METERED:unknown\n") is None
    monkeypatch.setenv("MPV_UOS_METERED", "1")
    assert rules.detect_metered() is True
    monkeypatch.setenv("MPV_UOS_METERED", "0")
    assert rules.detect_metered() is False
    monkeypatch.delenv("MPV_UOS_METERED")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert rules.detect_metered() is None      # no nmcli: cannot tell (treated as not metered)


# -- chain helpers and persistence --------------------------------------------------------------------------------


def test_names_and_templates():
    meta = {"title": 'Un "título": con/barras?', "date": "2026-09-30", "uploader": "", "feed": "180 grados", "id": "x"}
    assert render_name("{date} - {title}", meta) == "2026-09-30 - Un título con barras"
    assert render_name("{uploader} - {date} - {title}", meta) == "2026-09-30 - Un título con barras"
    assert render_name("{feed} - {title}", {**meta, "title": ""}) == "180 grados"
    assert safe_component("..") == "sin título" and len(safe_component("á" * 200).encode()) <= 150
    for bad in ("{fecha}", "sin campos", "{title}/x"):
        with pytest.raises(ValueError):
            check_template(bad)
    c = ChainConfig()
    assert not c.active() and c.steps() == []
    c.update({"loudnorm": 1, "subtitles": True, "translate": "ES", "rename": "{title}", "move_to": "~/B"})
    assert c.steps() == ["loudnorm", "rename", "move", "subtitles", "translate"] and c.translate == "es"
    with pytest.raises(ValueError):
        c.update({"translate": "español"})


def test_store_roundtrip(tmp_path):
    st = FeedStore(tmp_path / "feeds.json")
    s = Subscription(url="https://www.youtube.com/@x/videos", kind="channel", title="X")
    s.update({"keep": 5, "preset": "audio_mp3_192", "chain": {"loudnorm": True}})
    st.subs[s.id] = s
    st.settings.update({"window": "de 1 a 7", "max_items": 3, "chain": {"rename": "{date} - {title}"}})
    st.quota.items = 2
    st.save()
    again = FeedStore(tmp_path / "feeds.json")
    t = again.subs[s.id]
    assert t.keep == 5 and t.preset == "audio_mp3_192" and t.is_audio() and t.chain["loudnorm"] is True
    assert again.settings.window == "01:00-07:00" and again.settings.max_items == 3 and again.quota.items == 2
    assert again.settings.chain["rename"] == "{date} - {title}"
    for bad in ({"preset": "nope"}, {"keep": -1}, {"container": "avi"}, {"chain": {"rename": "{x}"}}):
        with pytest.raises(ValueError):
            Subscription(url="u", kind="rss").update(bad)
    with pytest.raises(ValueError):
        FeedSettings().update({"interval_h": 0.01})
