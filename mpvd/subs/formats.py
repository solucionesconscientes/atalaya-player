"""Load external subtitle files (SRT, WebVTT, ASS/SSA) into plain cues; always written back as SRT for mpv."""

from __future__ import annotations

import re
from pathlib import Path

from mpvd.asr.srt import Segment, parse_srt

ASS_TAG_RE = re.compile(r"\{[^}]*\}")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
VTT_TIME_RE = re.compile(r"(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}")


class SubtitleError(ValueError):
    pass


def read_text(path: Path) -> str:
    """UTF-8 (with or without BOM) first, then UTF-16 with BOM, then cp1252 as a last resort."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _vtt_time(t: str) -> float:
    t = t.strip().replace(",", ".")
    parts = t.split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_vtt(text: str) -> list[Segment]:
    segs: list[Segment] = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")
        b = b.strip().split(" ")[0]   # drop cue settings (align:start position:10%)
        try:
            start, end = _vtt_time(a), _vtt_time(b)
        except ValueError:
            continue
        body = " ".join(HTML_TAG_RE.sub("", ln) for ln in lines[idx + 1:]).strip()
        if body:
            segs.append(Segment(start, end, body))
    return segs


def _ass_time(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text: str) -> list[Segment]:
    segs: list[Segment] = []
    fmt: list[str] = []
    in_events = False
    for line in text.replace("\r", "").split("\n"):
        s = line.strip()
        if s.lower() == "[events]":
            in_events = True
            continue
        if s.startswith("[") and s.endswith("]"):
            in_events = False
            continue
        if not in_events:
            continue
        if s.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in s.split(":", 1)[1].split(",")]
            continue
        if not s.lower().startswith("dialogue:") or not fmt:
            continue
        fields = s.split(":", 1)[1].split(",", len(fmt) - 1)
        if len(fields) < len(fmt):
            continue
        row = dict(zip(fmt, [f.strip() for f in fields], strict=False))
        try:
            start, end = _ass_time(row["start"]), _ass_time(row["end"])
        except (KeyError, ValueError):
            continue
        body = ASS_TAG_RE.sub("", row.get("text", "")).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
        body = " ".join(body.split())
        if body and end > start:
            segs.append(Segment(start, end, body))
    segs.sort(key=lambda c: (c.start, c.end))
    return segs


def load_cues(path: str | Path) -> list[Segment]:
    p = Path(path)
    if not p.is_file():
        raise SubtitleError(f"no existe: {p}")
    text = read_text(p)
    ext = p.suffix.lower()
    if ext in (".ass", ".ssa") or "[Events]" in text[:5000]:
        cues = parse_ass(text)
    elif ext == ".vtt" or text.lstrip().startswith("WEBVTT"):
        cues = parse_vtt(text)
    else:
        cues = parse_srt(text)
        cues = [Segment(c.start, c.end, " ".join(HTML_TAG_RE.sub("", ASS_TAG_RE.sub("", c.text)).split())) for c in cues]
    cues = [c for c in cues if c.text.strip() and c.end > c.start]
    if not cues:
        raise SubtitleError(f"no hay cues legibles en {p.name}")
    return cues
