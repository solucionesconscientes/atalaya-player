"""Parser + model against real list excerpts (tests/fixtures/iptv) and the hand-made edge-case file."""

import json
from pathlib import Path

import pytest

from mpvd.iptv.m3u import parse_m3u
from mpvd.iptv.model import clean_title, dedupe, entry_to_channel
from mpvd.iptv.radiobrowser import RadioBrowser

FIX = Path(__file__).parent / "fixtures" / "iptv"


def load(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


def test_tdtchannels_tv_sample():
    pl = parse_m3u(load("tdtchannels_tv.sample.m3u8"))
    assert pl.kind == "channels" and len(pl.entries) == 50 and pl.warnings == []
    assert pl.epg_url == "https://www.tdtchannels.com/epg/TV.xml.gz"  # second #EXTM3U line carries it
    chans = [entry_to_channel(e, "tdt_tv", "tv", "es") for e in pl.entries]
    assert all(c.kind == "tv" and c.country == "es" for c in chans)
    assert len({c.group for c in chans}) == 30
    with_headers = [c for c in chans if c.headers]
    assert len(with_headers) >= 1 and all(set(c.headers) <= {"User-Agent", "Referer"} for c in with_headers)
    la1 = next(c for c in chans if c.name == "La 1")
    assert la1.tvg_id == "La1.TV" and la1.logo and la1.url.startswith("http")
    assert len(dedupe(chans)) == 50  # distinct URLs even when names repeat
    assert any(not c.name.isascii() for c in chans)


def test_tdtchannels_radio_sample():
    pl = parse_m3u(load("tdtchannels_radio.sample.m3u8"))
    assert len(pl.entries) == 20 and pl.warnings == []
    chans = [entry_to_channel(e, "tdt_radio", "radio", "es") for e in pl.entries]
    assert all(c.kind == "radio" for c in chans)
    assert sum(c.name == "Cadena SER" for c in chans) == 3 and len(dedupe(chans)) == 20
    assert all(c.group and c.group.startswith("Radio") for c in chans)


def test_iptv_org_sample():
    text = load("iptv_org_index.sample.m3u")
    assert "\r\n" in text
    pl = parse_m3u(text)
    assert len(pl.entries) == 51 and pl.warnings == []
    chans = [entry_to_channel(e, "iptv_org", "tv", None, True) for e in pl.entries]
    countries = {c.country for c in chans}
    assert len(countries) >= 25 and None not in countries
    assert {c.url.split(":", 1)[0] for c in chans} >= {"https", "http", "rtmp", "mmsh", "srt"}
    hdr = [c for c in chans if c.headers]
    assert len(hdr) >= 5 and all("Referer" in c.headers or "User-Agent" in c.headers for c in hdr)
    two_m = next(c for c in chans if c.name.startswith("2M Monde"))
    assert two_m.headers["Referer"] == "http://www.radio2m.ma/" and two_m.headers["User-Agent"].startswith("Mozilla/5.0")
    assert two_m.country == "ma" and two_m.extra.get("quality") == "360p" and "(" not in two_m.name
    assert any(c.extra.get("geo_blocked") == "1" for c in chans) and any(c.extra.get("not_24_7") == "1" for c in chans)
    assert all("[Not 24/7]" not in c.name and "[Geo-blocked]" not in c.name for c in chans)
    weather = next(c for c in chans if c.group == "Public;Weather")
    assert weather.category == "Public"
    assert any(c.logo is None for c in chans)  # tvg-logo="" -> None


def test_edge_cases_file():
    text = load("edge_cases.m3u")
    assert text.startswith("﻿")
    pl = parse_m3u(text)
    assert pl.kind == "channels"  # stray #EXT-X-* tags do not turn a channel list into HLS
    assert pl.header["url-tvg"] == "https://example.invalid/epg.xml.gz" and pl.epg_url == pl.header["url-tvg"]
    by_name = {e.name: e for e in pl.entries}
    assert "Sin atributos" in by_name and by_name["Duracion cero"].duration == 0
    assert by_name["Con duracion positiva (VOD)"].duration == 123.5
    assert "Canal, con coma, en el titulo" in by_name
    assert by_name["Coma en logo"].attrs["tvg-logo"].endswith("w_200,h_200,al_c/logo.png")
    assert by_name["Atributos sin comillas"].attrs["tvg-id"] == "SinComillas.TV"
    assert by_name["Comillas escapadas en atributo"].attrs["tvg-name"] == 'Canal "Escapado"'
    assert by_name["Comillas simples en atributos"].attrs["group-title"] == "Rarezas"
    assert by_name["Atributos con valor vacio"].attrs["tvg-logo"] == ""
    assert by_name["Titulo con espacios alrededor"].url == "https://example.invalid/espacios.m3u8"
    titulo_vacio = next(e for e in pl.entries if e.url.endswith("titulo-vacio.m3u8"))
    assert titulo_vacio.name == "Titulo.TV"  # falls back to tvg-id
    assert any("🎵" in n or "ñ" in n for n in by_name)
    assert by_name["Canal con EXTGRP"].group == "Grupo via EXTGRP"
    vlc = by_name["Canal con EXTVLCOPT y KODIPROP"]
    ch = entry_to_channel(vlc, "x")
    assert ch.headers == {"User-Agent": "Test/1.0", "Referer": "https://example.invalid/"}  # KODIPROP wins (last)
    assert ch.drm is True and ch.url.endswith("manifest.mpd")
    assert "Primer EXTINF sin URL (le sigue otro EXTINF)" not in by_name and "Segundo EXTINF seguido" in by_name
    assert "url-sin-extinf.m3u8" in by_name
    assert "variant_720p.m3u8" in by_name  # a bare URL after #EXT-X-STREAM-INF is still parsed
    dups = [e for e in pl.entries if e.url == "https://example.invalid/dup.m3u8"]
    assert len(dups) == 3 and len(dedupe([entry_to_channel(e, "x") for e in dups])) == 1
    assert by_name["srt sin extension"].url == "srt://192.0.2.1:4001"
    assert "ruta relativa (invalida en lista remota)" not in by_name
    assert by_name["URL con espacio y query"].url == "https://example.invalid/con espacio.m3u8?token=a b&x=1"
    assert by_name["Ultima entrada sin salto de linea final"].url.endswith("final.m3u8")
    joined = "\n".join(pl.warnings)
    assert "not a URL" in joined and "#EXTINF without URL" in joined and "URL without #EXTINF" in joined


def test_radio_browser_fixture_mapping():
    stations = json.loads(load("radio_browser_stations.sample.json"))
    assert len(stations) == 5
    chans = [RadioBrowser.to_channel(s) for s in stations]
    assert all(c.kind == "radio" and c.country == "es" and c.url.startswith("http") for c in chans)
    assert all(c.extra.get("stationuuid") for c in chans)
    countries = json.loads(load("radio_browser_countries.sample.json"))
    assert {c["iso_3166_1"] for c in countries} >= {"ES", "FR", "DE"}


@pytest.mark.parametrize(
    ("title", "name", "flags"),
    [
        ("12tv (720p)", "12tv", {"quality": "720p"}),
        ("6 TV Gorlovka (720p) [Geo-blocked]", "6 TV Gorlovka", {"quality": "720p", "geo_blocked": "1"}),
        ("12 TV Parma (540p) [Not 24/7]", "12 TV Parma", {"quality": "540p", "not_24_7": "1"}),
        ("Canal (HD) normal", "Canal (HD) normal", {}),
        ("Solo nombre", "Solo nombre", {}),
        ("[Not 24/7]", "[Not 24/7]", {"not_24_7": "1"}),
    ],
)
def test_clean_title(title, name, flags):
    assert clean_title(title) == (name, flags)
