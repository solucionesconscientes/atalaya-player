"""Subtitles the website offers for an internet video (H29, ADR-056): manual tracks and the automatic captions in the
video's own language, as clean SRT files in the cache (translated offline by subs.translate, saved by subs.save).

Facts checked on YouTube on 2026-09-30 (tmp/research-websubs, vendored yt-dlp 2026.08.19):
- ``-J`` has ``subtitles`` (manual) and ``automatic_captions``: ``{lang: [{ext, url, name, ...}]}`` with ext json3,
  srv1-3, ttml, srt, vtt. The automatic captions of the original language are ``<lang>-orig`` (URL ``kind=asr`` without
  ``tlang``); ``automatic_captions[<lang>]`` is the same track, and the other keys are YouTube's machine translations
  (``tlang=``), which answer HTTP 429 without a PO token: they are not offered (our own offline translation is).
- Entries with ``protocol: m3u8_native`` inside ``automatic_captions`` are the manual track over HLS: skipped.
- YouTube's native ``fmt=srt`` of automatic captions is already clean (one line per cue) but consecutive cues overlap by
  ~2.5 s (a rolling pair); its VTT repeats the previous line in every cue plus 10 ms "commit" cues. Both are rebuilt
  here as two-line cues that do not overlap. URLs expire after ~6 h: on 403/404/410 the ``-J`` is fetched again.
- yt-dlp's own ``--write-auto-subs`` is not used for this: with ``--sub-langs all`` it lists thousands of translated
  tracks (14 MB ``-J``, minutes in mpv's ytdl_hook).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mpvd.asr.srt import Segment, parse_srt, render_srt
from mpvd.subs.formats import HTML_TAG_RE, parse_vtt

PREFERRED_EXTS = ("srt", "vtt")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MIN_CUE = 0.05          # YouTube's 10 ms "commit" cues
TIME_TAG_RE = re.compile(r"<(\d{1,2}:)?\d{2}:\d{2}\.\d{3}>")   # per-word timing inside WebVTT cues
MAX_LINE_CHARS = 48     # two short rolling lines are merged into one line up to this length

LANG_NAMES = {"es": "Español", "en": "Inglés", "fr": "Francés", "de": "Alemán", "it": "Italiano", "pt": "Portugués",
              "ca": "Catalán", "gl": "Gallego", "eu": "Euskera", "nl": "Neerlandés", "ru": "Ruso", "ja": "Japonés",
              "zh": "Chino", "ko": "Coreano", "ar": "Árabe", "pl": "Polaco", "tr": "Turco", "uk": "Ucraniano",
              "sv": "Sueco", "hi": "Hindi", "el": "Griego", "fi": "Finés", "hu": "Húngaro", "ro": "Rumano",
              "vi": "Vietnamita", "th": "Tailandés", "iw": "Hebreo", "he": "Hebreo", "fa": "Persa", "sk": "Eslovaco",
              "sr": "Serbio", "cs": "Checo", "da": "Danés", "no": "Noruego", "id": "Indonesio"}


def base_lang(lang: str) -> str:
    return re.split(r"[-_]", lang or "", maxsplit=1)[0].lower()


def lang_label(lang: str, fallback: str = "") -> str:
    """«Español (España)» for es-ES, «Español» for es; the site's own name when the language is unknown here."""
    base = base_lang(lang)
    name = LANG_NAMES.get(base)
    if not name:
        return fallback or lang
    region = lang.split("-", 1)[1] if "-" in lang and not lang.endswith("-orig") else ""
    regions = {"ES": "España", "419": "Latinoamérica", "MX": "México", "BR": "Brasil", "PT": "Portugal",
               "US": "EE. UU.", "GB": "Reino Unido", "TW": "Taiwán", "CN": "China", "HK": "Hong Kong"}
    return f"{name} ({regions.get(region, region)})" if region else name


def _usable(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries or [] if isinstance(e, dict) and e.get("url") and not e.get("protocol")
            and e.get("ext") in PREFERRED_EXTS]


def _pick(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = _usable(entries)
    for ext in PREFERRED_EXTS:
        for e in usable:
            if e.get("ext") == ext:
                return e
    return None


def is_translation(entry: dict[str, Any]) -> bool:
    return "tlang=" in str(entry.get("url") or "")


def list_tracks(info: dict[str, Any], prefer: str = "es") -> list[dict[str, Any]]:
    """Tracks worth offering: every manual one and the automatic captions of the original language (never YouTube's
    machine translations). ``[{lang, kind: manual|auto, label, name, ext}]``; the user's language and the original
    first."""
    out: list[dict[str, Any]] = []
    for lang, entries in (info.get("subtitles") or {}).items():
        if lang == "live_chat":
            continue
        e = _pick(entries)
        if e is not None:
            out.append({"lang": lang, "kind": "manual", "name": str(e.get("name") or ""), "ext": e["ext"],
                        "label": lang_label(lang, str(e.get("name") or lang))})
    auto = info.get("automatic_captions") or {}
    orig_keys = [k for k in auto if k.endswith("-orig")]
    if not orig_keys and info.get("language") and info["language"] in auto:
        orig_keys = [info["language"]]
    for key in orig_keys:
        e = _pick(auto[key])
        if e is None or is_translation(e):
            continue
        lang = key.removesuffix("-orig")
        out.append({"lang": lang, "kind": "auto", "key": key, "name": str(e.get("name") or ""), "ext": e["ext"],
                    "label": lang_label(lang, str(e.get("name") or lang)) + " (automáticos)"})
    original = base_lang(str(info.get("language") or ""))

    def order(t: dict[str, Any]) -> tuple[int, int, str]:
        b = base_lang(t["lang"])
        return (0 if b == base_lang(prefer) else 1 if b == original else 2, 0 if t["kind"] == "manual" else 1,
                t["label"])
    return sorted(out, key=order)


def entry_for(info: dict[str, Any], lang: str, kind: str) -> dict[str, Any] | None:
    if kind == "manual":
        return _pick((info.get("subtitles") or {}).get(lang) or [])
    auto = info.get("automatic_captions") or {}
    for key in (f"{lang}-orig", lang):
        e = _pick(auto.get(key) or [])
        if e is not None and not is_translation(e):
            return e
    return None


# -- cleaning ---------------------------------------------------------------------------------------------------

def rolling_lines_from_vtt(text: str) -> list[Segment]:
    """YouTube automatic VTT → one cue per new line: each real cue shows the previous line plus the new one (with
    word timing tags); the 10 ms cues only repeat what was said."""
    lines: list[Segment] = []
    for block in text.replace("\r", "").split("\n\n"):
        rows = block.split("\n")
        idx = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if idx is None:
            continue
        a, b = rows[idx].split("-->")
        try:
            start, end = _time(a), _time(b.strip().split(" ")[0])
        except ValueError:
            continue
        if end - start < MIN_CUE:
            continue
        body = [" ".join(HTML_TAG_RE.sub("", TIME_TAG_RE.sub("", r)).split()) for r in rows[idx + 1:]]
        body = [r for r in body if r]
        if not body:
            continue
        new = body[-1]
        if lines and lines[-1].text == new:
            lines[-1].end = max(lines[-1].end, end)
            continue
        lines.append(Segment(start, end, new))
    return lines


def _time(t: str) -> float:
    t = t.strip().replace(",", ".")
    parts = t.split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


def pair_rolling_lines(lines: list[Segment]) -> list[Segment]:
    """Rolling one-line captions (each overlapping the next) → ordinary cues of up to two lines that never overlap:
    a line starts a cue, the next one joins it (second row, or the same row when both are short), and the cue lasts
    until the following cue starts."""
    cues: list[list[Segment]] = []
    for ln in lines:
        if cues and len(cues[-1]) == 1:
            cues[-1].append(ln)
        else:
            cues.append([ln])
    out: list[Segment] = []
    for i, group in enumerate(cues):
        start = group[0].start
        nxt = cues[i + 1][0].start if i + 1 < len(cues) else None
        end = max(s.end for s in group)
        if nxt is not None:
            end = min(end, nxt) if end > nxt else end
        texts = [s.text for s in group]
        text = " ".join(texts) if len(" ".join(texts)) <= MAX_LINE_CHARS else "\n".join(texts)
        out.append(Segment(start, max(end, start + 0.3), text))
    return out


def overlapping(cues: list[Segment]) -> bool:
    """Most consecutive cues overlap: YouTube's rolling automatic captions (native SRT)."""
    if len(cues) < 4:
        return False
    n = sum(1 for a, b in zip(cues, cues[1:]) if b.start < a.end - 0.2)
    return n >= (len(cues) - 1) * 0.6


def to_cues(text: str, ext: str, kind: str) -> list[Segment]:
    """Downloaded subtitle text → clean cues (manual tracks as they are; automatic ones de-rolled)."""
    if ext == "vtt":
        if kind == "auto" and re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}><c>", text):
            return pair_rolling_lines(rolling_lines_from_vtt(text))
        cues = parse_vtt(text, lines="keep")
    else:
        cues = parse_srt(text, keep_lines=True)
    cues = [Segment(c.start, c.end, HTML_TAG_RE.sub("", TIME_TAG_RE.sub("", c.text)).strip()) for c in cues]
    cues = [c for c in cues if c.text and c.end - c.start >= MIN_CUE]
    if kind == "auto" and overlapping(cues):
        return pair_rolling_lines(cues)
    return cues


def fetch_text(url: str, timeout: float = 30.0) -> str:
    """GET a subtitle URL (raises urllib.error.HTTPError; 403/404/410 = expired link, 429 = rate limited)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "es,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 - http(s) URL from yt-dlp's -J
        raw = resp.read()
    return raw.decode("utf-8-sig", errors="replace")


def write_srt(dest: Path, cues: list[Segment]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(render_srt(cues), encoding="utf-8")
    tmp.replace(dest)


EXPIRED = (403, 404, 410)
