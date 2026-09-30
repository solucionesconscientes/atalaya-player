"""A small music folder generated with ffmpeg for the H32 tests: tones with different tags and loudness, an album with
cover.jpg, one with the cover embedded in the MP3, one track with ReplayGain tags and one without any tag."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-y", *args], check=True, capture_output=True)


def tone(dest: Path, freq: int, seconds: float, volume_db: float, tags: dict[str, str], cover: Path | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta: list[str] = []
    for k, v in tags.items():
        meta += ["-metadata", f"{k}={v}"]
    src = ["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:sample_rate=44100"]
    af = ["-af", f"volume={volume_db}dB"]
    if cover is not None:
        _ff(*src, "-i", str(cover), "-map", "0:a", "-map", "1:v", *af, "-c:a", "libmp3lame", "-b:a", "96k",
            "-c:v", "mjpeg", "-disposition:v:0", "attached_pic", "-id3v2_version", "3", *meta, str(dest))
    else:
        codec = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame", "-b:a", "96k"],
                 ".ogg": ["-c:a", "libvorbis", "-q:a", "2"], ".opus": ["-c:a", "libopus", "-b:a", "48k"]}[dest.suffix]
        _ff(*src, *af, *codec, *meta, str(dest))
    return dest


def image(dest: Path, color: str = "red") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ff("-f", "lavfi", "-i", f"color=c={color}:s=64x64", "-frames:v", "1", str(dest))
    return dest


def build_music(root: Path) -> dict[str, Path]:
    """root/
         Mónica Naranjo/1997 - Palabra de mujer/{01,02}.flac + cover.jpg   (album artist, genres «Pop; Balada», loud/quiet)
         Rock/Héroes.mp3                                                     (embedded cover, genre Rock, 1985)
         Rock/Tagged.flac                                                    (REPLAYGAIN_TRACK_GAIN tag)
         sueltas/pista sin etiquetas.ogg                                     (no tags at all)
    """
    alb = root / "Mónica Naranjo" / "1997 - Palabra de mujer"
    common = {"album_artist": "Mónica Naranjo", "artist": "Mónica Naranjo", "album": "Palabra de mujer",
              "date": "1997-05-01", "genre": "Pop; Balada"}
    p = {
        "t1": tone(alb / "01 - Sobreviviré.flac", 440, 4, -3, {**common, "title": "Sobreviviré", "track": "1/2"}),
        "t2": tone(alb / "02 - Desátame.flac", 660, 4, -15, {**common, "title": "Desátame", "track": "2/2"}),
    }
    image(alb / "cover.jpg")
    cover = image(root / "tmp-cover.png", "blue")
    p["emb"] = tone(root / "Rock" / "Héroes.mp3", 330, 4, -9, {"artist": "Héroes del Silencio", "album": "Senderos",
                                                             "title": "Entre dos tierras", "date": "1985",
                                                             "genre": "Rock", "track": "3"}, cover=cover)
    cover.unlink()
    p["tagged"] = tone(root / "Rock" / "Tagged.flac", 550, 3, -6, {
        "artist": "Otro", "album": "Con ganancia", "title": "Etiquetada", "date": "2010", "genre": "Rock",
        "REPLAYGAIN_TRACK_GAIN": "-4.20 dB", "REPLAYGAIN_TRACK_PEAK": "0.250000"})
    p["loose"] = tone(root / "sueltas" / "pista sin etiquetas.ogg", 220, 3, -12, {})
    return p
