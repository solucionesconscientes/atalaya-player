"""Load external subtitle files (SRT, WebVTT, ASS/SSA) into plain cues; always written back as SRT for mpv.

Line breaks inside a cue: ``lines="dialogue"`` (default) keeps them only for dialogue cues (later lines start with a
dash, ``- ¿Vienes?\n- Sí.``) and joins the rest with spaces; ``"keep"`` keeps every line (saving a file); ``"join"``
always joins."""

from __future__ import annotations

import re
from pathlib import Path

from mpvd.asr.srt import DASH_START, Segment, parse_srt

ASS_TAG_RE = re.compile(r"\{[^}]*\}")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
VTT_TIME_RE = re.compile(r"(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}")


class SubtitleError(ValueError):
    pass


def join_lines(lines: list[str], mode: str = "dialogue") -> str:
    """Collapse whitespace per line and join the lines of one cue according to ``mode`` (see module docstring)."""
    rows = [" ".join(ln.split()) for ln in lines]
    rows = [r for r in rows if r]
    if mode == "keep" or (mode == "dialogue" and len(rows) >= 2 and all(DASH_START.match(r) for r in rows[1:])):
        return "\n".join(rows)
    return " ".join(rows)


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


def parse_vtt(text: str, lines: str = "dialogue") -> list[Segment]:
    segs: list[Segment] = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        rows = [ln for ln in block.split("\n") if ln.strip()]
        if not rows or rows[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        idx = next((i for i, ln in enumerate(rows) if "-->" in ln), None)
        if idx is None:
            continue
        a, b = rows[idx].split("-->")
        b = b.strip().split(" ")[0]   # drop cue settings (align:start position:10%)
        try:
            start, end = _vtt_time(a), _vtt_time(b)
        except ValueError:
            continue
        body = join_lines([HTML_TAG_RE.sub("", ln) for ln in rows[idx + 1:]], lines)
        if body:
            segs.append(Segment(start, end, body))
    return segs


def _ass_time(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text: str, lines: str = "dialogue") -> list[Segment]:
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
        body = ASS_TAG_RE.sub("", row.get("text", "")).replace("\\h", " ")
        body = join_lines(re.split(r"\\[Nn]", body), lines)
        if body and end > start:
            segs.append(Segment(start, end, body))
    segs.sort(key=lambda c: (c.start, c.end))
    return segs


def detect_format(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    if ext in (".ass", ".ssa") or "[Events]" in text[:5000]:
        return "ass"
    if ext == ".vtt" or text.lstrip().startswith("WEBVTT"):
        return "vtt"
    return "srt"


def load_cues(path: str | Path, lines: str = "dialogue") -> list[Segment]:
    p = Path(path)
    if not p.is_file():
        raise SubtitleError(f"no existe: {p}")
    text = read_text(p)
    fmt = detect_format(p, text)
    if fmt == "ass":
        cues = parse_ass(text, lines)
    elif fmt == "vtt":
        cues = parse_vtt(text, lines)
    else:
        cues = [Segment(c.start, c.end, join_lines(HTML_TAG_RE.sub("", ASS_TAG_RE.sub("", c.text)).split("\n"), lines))
                for c in parse_srt(text, keep_lines=True)]
    cues = [c for c in cues if c.text.strip() and c.end > c.start]
    if not cues:
        raise SubtitleError(f"no hay cues legibles en {p.name}")
    return cues
