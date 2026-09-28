"""The generated test media matches its manifest (ffprobe)."""

import json
import subprocess
from pathlib import Path

import pytest


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@pytest.mark.parametrize("name", ["video30.mkv", "chapters.mkv", "voz_es.flac", "voz_en.flac", "voz_es_en.mkv"])
def test_media_matches_manifest(media_dir: Path, media_manifest, name: str):
    spec = media_manifest[name]
    info = _probe(media_dir / name)
    assert abs(float(info["format"]["duration"]) - spec["duration"]) < 0.5
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    assert bool(video) == spec["video"]
    if "audio_tracks" in spec:
        assert len(audio) == spec["audio_tracks"]
    if "audio_langs" in spec:
        assert [s["tags"]["language"] for s in audio] == spec["audio_langs"]
    if "sample_rate" in spec:
        assert int(audio[0]["sample_rate"]) == spec["sample_rate"]
        assert audio[0]["channels"] == spec["channels"]
    if "chapters" in spec:
        assert [c["tags"]["title"] for c in info["chapters"]] == spec["chapters"]


def test_voice_sidecars_have_keywords(media_dir: Path):
    for lang in ("es", "en"):
        meta = json.loads((media_dir / f"voz_{lang}.json").read_text(encoding="utf-8"))
        assert meta["lang"] == lang
        assert len(meta["keywords"]) >= 5
        assert all(k.lower() in meta["text"].lower() for k in meta["keywords"])
