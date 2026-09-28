"""Analysis of real ``-J`` fixtures (tests/fixtures/ytdlp, recorded from yt-dlp 2026.08.19)."""

import json
from pathlib import Path

from mpvd.ytdl.info import analyze, classify, flat_entries, format_row, group_formats, human_size, summary

FIX = Path(__file__).parent / "fixtures" / "ytdlp"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_youtube_groups_video_and_audio_only():
    info = load("youtube_bbb.json")
    g = group_formats(info)
    assert not g["combined"], "YouTube has no combined formats any more (docs/YTDLP.md §8)"
    assert len(g["video"]) >= 30 and len(g["audio"]) >= 10
    total = sum(1 for f in info["formats"] if f.get("protocol") != "mhtml")
    assert len(g["video"]) + len(g["audio"]) == total  # storyboards excluded, nothing else lost
    best = g["video"][0]
    assert best["height"] == 2160 and best["fps"] >= 50 and best["label"].startswith("2160p")
    assert all(g["video"][i]["height"] >= g["video"][i + 1]["height"] for i in range(len(g["video"]) - 1))
    audio_ids = {r["id"] for r in g["audio"]}
    assert "251" in audio_ids and "140" in audio_ids
    opus = next(r for r in g["audio"] if r["id"] == "251")
    assert opus["acodec_name"] == "Opus" and "Opus" in opus["label"] and opus["kind"] == "audio"
    hdr = [r for r in g["video"] if r["hdr"]]
    assert all("HDR" in r["label"] for r in hdr)
    hls = [r for r in g["video"] + g["audio"] if "HLS" in r["hint"]]
    assert hls, "m3u8 formats are flagged as HLS"


def test_youtube_summary_and_selected():
    info = load("youtube_bbb.json")
    s = summary(info)
    assert s["id"] == "aqz-KE-bpKQ" and s["duration"] == 635 and s["extractor"] == "youtube"
    assert s["selected"] == ["401", "251"] and s["type"] == "video" and s["live_status"] == "not_live"
    a = analyze(info)
    assert a["counts"]["combined"] == 0 and a["has_combined"] is False and a["thumbnail"]


def test_archive_org_null_codecs_are_combined():
    info = load("archive_countdown.json")
    g = group_formats(info)
    assert len(g["combined"]) == 3 and len(g["audio"]) == 1 and not g["video"]
    mp4 = next(r for r in g["combined"] if r["id"] == "3")
    assert mp4["height"] == 480 and mp4["ext"] == "mp4" and "MB" in mp4["hint"]
    assert g["audio"][0]["ext"] == "mp3"
    s = summary(info)
    assert s["extractor"] == "archive.org" and s["duration"] and s["duration"] < 20


def test_classify_edge_cases():
    assert classify({"vcodec": "none", "acodec": "opus"}) == "audio"
    assert classify({"vcodec": "avc1", "acodec": "none"}) == "video"
    assert classify({"vcodec": None, "acodec": None, "width": 320}) == "combined"
    assert classify({"vcodec": "none", "acodec": None}) == "audio"  # HLS audio without codec info
    row = format_row({"format_id": "x", "ext": "mp4", "vcodec": "vp09.00.40.08", "acodec": "none", "height": 1080,
                      "fps": 60, "filesize_approx": 123456789, "has_drm": True, "dynamic_range": "HDR10"})
    assert row["label"] == "1080p60 · HDR · VP9 · mp4" and row["hint"].startswith("~123.5 MB") and "DRM" in row["hint"]
    assert human_size(999) == "999 B" and human_size(1_500_000) == "1.5 MB"


def test_flat_playlists():
    pl = load("playlist_flat.json")
    rows = flat_entries(pl)
    assert len(rows) == 5 and all(r["url"].startswith("https://www.youtube.com/watch?v=") for r in rows)
    assert summary(pl)["type"] == "playlist"
    search = load("playlist_flat_search.json")
    assert len(flat_entries(search)) == 5 and all(r["title"] for r in flat_entries(search))
