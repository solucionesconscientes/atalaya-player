"""M3U parser, channel normalization (headers -> mpv options), search index and store."""

import pytest

from mpvd.iptv.index import SearchIndex, normalize
from mpvd.iptv.m3u import parse_attrs, parse_m3u
from mpvd.iptv.model import Channel, dedupe, entry_to_channel, split_pipe_headers
from mpvd.iptv.store import IptvStore

SAMPLE = (
    "﻿#EXTM3U url-tvg=\"https://example.org/epg.xml\" x-other=1\r\n"
    "\r\n"
    "#EXTINF:-1 tvg-id=\"La1.es\" tvg-name=\"La 1\" tvg-logo=\"https://x/la1.png\" group-title=\"Generalistas\",La 1\r\n"
    "#EXTVLCOPT:http-user-agent=Mozilla/5.0 (X11)\r\n"
    "#EXTVLCOPT:http-referrer=https://www.rtve.es/\r\n"
    "https://ztnr.rtve.es/ztnr/1688877.m3u8\r\n"
    "#EXTINF:0 group-title=\"News, Politics\" tvg-country=ES,Canal, con coma\n"
    "#KODIPROP:inputstream.adaptive.manifest_type=hls\n"
    "#KODIPROP:inputstream.adaptive.stream_headers=User-Agent=VLC%2F3&Referer=https%3A%2F%2Fr.example%2F\n"
    "http://example.org/news.m3u8|Origin=https://o.example\n"
    "#EXTINF:-1 radio=\"true\" tvg-chno=7,Radio 3\n"
    "https://radio.example/r3.mp3\n"
    "#EXTGRP:Deportes\n"
    "#EXTINF:-1,Sin atributos\n"
    "https://example.org/sport.m3u8\n"
    "https://example.org/bare.m3u8\n"
    "#EXTINF:-1 tvg-id=\"dup\",Duplicado\n"
    "https://example.org/dup.m3u8\n"
    "#EXTINF:-1 tvg-id=\"dup\",Duplicado otra vez\n"
    "https://example.org/dup.m3u8\n"
    "#EXTINF:-1,Huérfano uno\n"
    "#EXTINF:-1,Huérfano dos\n"
    "https://example.org/two.m3u8\n"
    "# comentario libre\n"
    "#EXTINF:-1 tvg-id=\"drm\",Con DRM\n"
    "#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha\n"
    "#KODIPROP:inputstream.adaptive.license_key=https://lic.example/|Content-Type=|R{SSM}|\n"
    "https://example.org/drm.mpd\n"
    "esto no es una url\n"
    "#EXTINF:-1,Final sin url\n"
)


def test_parse_header_entries_and_warnings():
    pl = parse_m3u(SAMPLE)
    assert pl.kind == "channels"
    assert pl.epg_url == "https://example.org/epg.xml" and pl.header["x-other"] == "1"
    names = [e.name for e in pl.entries]
    assert names == ["La 1", "Canal, con coma", "Radio 3", "Sin atributos", "bare.m3u8", "Duplicado",
                     "Duplicado otra vez", "Huérfano dos", "Con DRM"]
    e0 = pl.entries[0]
    assert e0.attrs == {"tvg-id": "La1.es", "tvg-name": "La 1", "tvg-logo": "https://x/la1.png", "group-title": "Generalistas"}
    assert e0.vlcopts == {"http-user-agent": "Mozilla/5.0 (X11)", "http-referrer": "https://www.rtve.es/"}
    assert e0.url == "https://ztnr.rtve.es/ztnr/1688877.m3u8" and e0.line == 3
    e1 = pl.entries[1]
    assert e1.attrs["group-title"] == "News, Politics" and e1.attrs["tvg-country"] == "ES" and e1.duration == 0
    assert e1.kodiprops["inputstream.adaptive.manifest_type"] == "hls"
    assert pl.entries[3].group == "Deportes" and pl.entries[3].group_title == "Deportes"
    assert pl.entries[4].group == "Deportes"  # #EXTGRP applies until changed
    joined = "\n".join(pl.warnings)
    assert "URL without #EXTINF" in joined and "#EXTINF without URL" in joined and "not a URL" in joined
    assert "end of file" in joined


def test_hls_playlists_are_not_channel_lists():
    hls = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg1.ts\n#EXTINF:6.0,\nseg2.ts\n"
    pl = parse_m3u(hls)
    assert pl.kind == "hls" and pl.entries == []
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\nhttp://x/low.m3u8\n"
    assert parse_m3u(master).kind == "hls"


def test_parse_attrs_variants():
    assert parse_attrs('a="1" b=\'two\' c=3 D-E="x y"') == {"a": "1", "b": "two", "c": "3", "d-e": "x y"}
    assert parse_attrs('tvg-logo="" group-title="A,B"') == {"tvg-logo": "", "group-title": "A,B"}


def test_entry_to_channel_headers_and_kinds():
    pl = parse_m3u(SAMPLE)
    chans = [entry_to_channel(e, "tdt_tv") for e in pl.entries]
    la1 = chans[0]
    assert la1.kind == "tv" and la1.country == "es" and la1.tvg_id == "La1.es" and la1.logo.endswith("la1.png")
    assert la1.headers == {"User-Agent": "Mozilla/5.0 (X11)", "Referer": "https://www.rtve.es/"}
    assert la1.mpv_options() == {"force-media-title": "La 1", "user-agent": "Mozilla/5.0 (X11)",
                                 "referrer": "https://www.rtve.es/"}
    news = chans[1]
    assert news.url == "http://example.org/news.m3u8"  # pipe headers stripped from the URL
    assert news.headers == {"User-Agent": "VLC/3", "Referer": "https://r.example/", "Origin": "https://o.example"}
    assert news.country == "es" and news.group == "News, Politics" and news.extra == {"inputstream.adaptive.manifest_type": "hls"}
    assert news.mpv_options()["http-header-fields"] == "Origin: https://o.example"
    radio = chans[2]
    assert radio.kind == "radio" and radio.chno == 7
    assert chans[3].group == "Deportes"
    drm = chans[-1]
    assert drm.drm is True and "inputstream.adaptive.license_type" in drm.extra
    assert len(dedupe(chans)) == len(chans) - 1
    assert chans[5].id == chans[6].id and chans[5].id != chans[0].id


def test_http_header_fields_escapes_commas():
    ch = Channel(id="x", name="n", url="u", kind="tv", source="s", headers={"Cookie": "a=1, b=2", "Origin": "o"})
    assert ch.mpv_options()["http-header-fields"] == "%16%Cookie: a=1, b=2,Origin: o"


def test_split_pipe_headers():
    assert split_pipe_headers("http://x/a.m3u8") == ("http://x/a.m3u8", {})
    url, h = split_pipe_headers("http://x/a.m3u8|User-Agent=Mozilla%2F5.0&referer=https://r/")
    assert url == "http://x/a.m3u8" and h == {"User-Agent": "Mozilla/5.0", "Referer": "https://r/"}


def test_normalize_and_search():
    assert normalize("  Canción  Nº-1 (Español)") == "cancion no 1 espanol"
    chans = [
        Channel(id="1", name="La 1", url="u1", kind="tv", source="tdt", group="Generalistas", country="es"),
        Channel(id="2", name="La 2", url="u2", kind="tv", source="tdt", group="Generalistas"),
        Channel(id="3", name="Radio Clásica", url="u3", kind="radio", source="tdt", group="Radio"),
        Channel(id="4", name="Clásica FM", url="u4", kind="radio", source="rb"),
        Channel(id="5", name="Telecinco", url="u5", kind="tv", source="tdt", tvg_id="Telecinco.es"),
        Channel(id="6", name="Antena 3", url="u6", kind="tv", source="tdt", category="Generalistas"),
    ]
    idx = SearchIndex(chans)
    assert len(idx) == 6
    assert [c.id for c in idx.search("clasica")] == ["4", "3"]  # prefix match ranks first
    assert [c.id for c in idx.search("CLÁSICA", kind="radio", source="tdt")] == ["3"]
    assert [c.id for c in idx.search("la 1")] == ["1"]
    assert {c.id for c in idx.search("generalistas")} == {"1", "2", "6"}
    assert idx.search("") == [] and idx.search("zzz") == []
    assert [c.id for c in idx.search("tele")] == ["5"]
    assert len(idx.search("la", limit=1)) == 1


def test_store_favorites_recents_sources_health(tmp_path):
    st = IptvStore(tmp_path / "iptv.sqlite3", max_recents=3)
    a = Channel(id="a", name="A", url="ua", kind="tv", source="s")
    b = Channel(id="b", name="B", url="ub", kind="radio", source="s")
    assert st.add_favorite(a) and not st.add_favorite(a)
    assert st.toggle_favorite(b) is True and st.favorite_ids() == {"a", "b"}
    assert [c.name for c in st.favorites()] == ["A", "B"]
    assert st.toggle_favorite(a) is False and st.is_favorite("a") is False and st.remove_favorite("zz") is False
    for i in range(5):
        st.touch_recent(Channel(id=f"r{i}", name=f"R{i}", url=f"u{i}", kind="tv", source="s"))
    st.touch_recent(Channel(id="r2", name="R2", url="u2", kind="tv", source="s"))
    assert [c.id for c in st.recents()] == ["r2", "r4", "r3"]
    st.clear_recents()
    assert st.recents() == []
    st.add_source("user:mine", "Mi lista", "https://x/list.m3u", "tv")
    st.add_source("user:mine", "Mi lista 2", "https://x/list2.m3u", "radio")
    assert st.sources()[0]["name"] == "Mi lista 2" and st.sources()[0]["enabled"] is True
    assert st.set_source_enabled("user:mine", False) and st.sources()[0]["enabled"] is False
    assert st.remove_source("user:mine") and st.sources() == []
    st.set_health("a", False, "timeout")
    st.set_health("a", True)
    assert st.health()["a"]["ok"] is True
    st.close()


@pytest.mark.parametrize("text", ["", "#EXTM3U\n", "\n\n#EXTM3U\r\n\r\n"])
def test_empty_lists(text):
    pl = parse_m3u(text)
    assert pl.entries == [] and pl.kind == "channels"
