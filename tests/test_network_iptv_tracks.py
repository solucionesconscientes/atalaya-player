"""H30 with a real channel (needs internet): La 1 of RTVE from the TDTChannels list — its master playlist and the
track-list mpv really shows get readable names (Versión original, Audiodescripción, WebVTT subtitles).

Skipped when the list no longer carries RTVE's own stream or it does not open from here (geo-blocking abroad)."""

from __future__ import annotations

import urllib.request

import pytest

from mpvd.iptv import tracks as T
from mpvd.iptv.m3u import parse_m3u
from mpvd.iptv.model import default_user_agent, entry_to_channel
from mpvd.iptv.sources import BUILTIN_SOURCES

pytestmark = pytest.mark.network


def fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": default_user_agent(), "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URLs
        return resp.read(1 << 22).decode("utf-8", "replace")


def test_rtve_la1_tracks_have_readable_names(mpv_headless):
    tdt = next(s for s in BUILTIN_SOURCES if s.id == "tdt_tv")
    chans = [entry_to_channel(e, "tdt_tv") for e in parse_m3u(fetch(tdt.url)).entries]
    la1 = next((c for c in chans if c.name == "La 1" and "rtvelivestream.rtve.es" in c.url), None)
    if la1 is None:
        pytest.skip("TDTChannels no longer lists RTVE's own stream of La 1")
    try:
        master = fetch(la1.url)
    except OSError as exc:
        pytest.skip(f"La 1 does not answer from here: {exc}")
    s = T.summary(T.parse_master_media(master), "master")
    print("master:", s)
    assert "CC" in s["badges"] and "Español" in [x["label"] for x in s["subs"]]

    h = mpv_headless
    h.command("loadfile", la1.url, "replace", -1, la1.mpv_options())
    try:
        h.wait_property("track-list/count", lambda v: isinstance(v, int) and v >= 3, timeout=40)
    except TimeoutError:
        pytest.skip("La 1 did not open in mpv from here (geo-blocked?)")
    named = T.label_player_tracks(h.get("track-list"), T.parse_master_media(master))
    print("mpv:", [(t["type"], t["id"], t["label"]) for t in named])
    audio = [t["label"] for t in named if t["type"] == "audio"]
    subs = [t["label"] for t in named if t["type"] == "sub"]
    assert audio[0] == "Español" and len(audio) == len(set(audio))  # one entry per real track
    assert set(subs) >= {"Español", "Inglés"}
    assert [a["label"] for a in s["audio"]] == audio  # the master and the player agree
