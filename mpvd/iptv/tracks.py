"""Audio and subtitle tracks of a channel with readable Spanish names, and the CC / VO / AD badges of the lists (H30).

Two inputs describe the same thing:

* the ``#EXT-X-MEDIA`` lines of an HLS master playlist (``parse_master_media``): LANGUAGE, NAME, CHARACTERISTICS,
  FORCED... (RTVE: audio ``spa``/``qaa`` = original/``ads`` = audio description, WebVTT subtitles es/en/gl/ca/eu;
  3Cat: ``qad`` = audio description), and
* mpv's ``track-list`` while the channel plays (``label_player_tracks``): FFmpeg keeps LANGUAGE and the
  ``describes-video`` characteristic (``visual-impaired``) but drops NAME, and repeats the muxed audio once per
  variant (one HLS "program" each).

mpv track titles cannot be changed (``track-list/N/title`` is read-only, verified with mpv 0.41), so the names are
used by mu-iptv's own menu and OSD. Pure functions; docs/FUENTES_IPTV.md has what real channels carry.
"""

from __future__ import annotations

import gettext
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from mpvd.iptv.index import normalize
from mpvd.iptv.model import parse_hls_attrs

ISO_DIRS = [Path("/usr/share"), Path("/usr/local/share")]

# Names that read better than the ISO ones ("Vasco", "Español; Castellano", "Holandés, Flamenco") and the fallback
# for systems without iso-codes (Windows, macOS): the languages seen on TV lists.
LANG_NAMES = {
    "es": "Español", "ca": "Catalán", "gl": "Gallego", "eu": "Euskera", "ast": "Asturiano", "oc": "Aranés",
    "en": "Inglés", "fr": "Francés", "de": "Alemán", "it": "Italiano", "pt": "Portugués", "nl": "Neerlandés",
    "ar": "Árabe", "zh": "Chino", "ja": "Japonés", "ko": "Coreano", "ru": "Ruso", "uk": "Ucraniano", "pl": "Polaco",
    "ro": "Rumano", "el": "Griego", "tr": "Turco", "sv": "Sueco", "da": "Danés", "no": "Noruego", "nb": "Noruego",
    "fi": "Finés", "is": "Islandés", "hu": "Húngaro", "cs": "Checo", "sk": "Eslovaco", "bg": "Búlgaro",
    "hr": "Croata", "sr": "Serbio", "sl": "Esloveno", "he": "Hebreo", "hi": "Hindi", "fa": "Persa", "ur": "Urdu",
    "bn": "Bengalí", "ta": "Tamil", "id": "Indonesio", "ms": "Malayo", "th": "Tailandés", "vi": "Vietnamita",
    "tl": "Tagalo", "sq": "Albanés", "hy": "Armenio", "ka": "Georgiano", "az": "Azerí", "kk": "Kazajo",
    "lt": "Lituano", "lv": "Letón", "et": "Estonio", "mk": "Macedonio", "bs": "Bosnio", "ga": "Irlandés",
    "cy": "Galés", "la": "Latín", "sw": "Suajili", "am": "Amárico", "so": "Somalí", "ku": "Kurdo", "ps": "Pastún",
}
# 3-letter codes of the languages above (ISO 639-2 T and B) for systems without iso-codes.
_ISO3_FALLBACK = {
    "spa": "es", "cat": "ca", "glg": "gl", "eus": "eu", "baq": "eu", "eng": "en", "fra": "fr", "fre": "fr",
    "deu": "de", "ger": "de", "ita": "it", "por": "pt", "nld": "nl", "dut": "nl", "ara": "ar", "zho": "zh",
    "chi": "zh", "jpn": "ja", "kor": "ko", "rus": "ru", "ukr": "uk", "pol": "pl", "ron": "ro", "rum": "ro",
    "ell": "el", "gre": "el", "tur": "tr", "swe": "sv", "dan": "da", "nor": "no", "nob": "nb", "fin": "fi",
    "isl": "is", "ice": "is", "hun": "hu", "ces": "cs", "cze": "cs", "slk": "sk", "slo": "sk", "bul": "bg",
    "hrv": "hr", "srp": "sr", "slv": "sl", "heb": "he", "hin": "hi", "fas": "fa", "per": "fa", "urd": "ur",
    "ben": "bn", "tam": "ta", "ind": "id", "msa": "ms", "may": "ms", "tha": "th", "vie": "vi", "tgl": "tl",
    "sqi": "sq", "alb": "sq", "hye": "hy", "arm": "hy", "kat": "ka", "geo": "ka", "aze": "az", "kaz": "kk",
    "lit": "lt", "lav": "lv", "est": "et", "mkd": "mk", "mac": "mk", "bos": "bs", "gle": "ga", "cym": "cy",
    "wel": "cy", "lat": "la", "swa": "sw", "amh": "am", "som": "so", "kur": "ku", "pus": "ps", "oci": "oc",
}

# Codes that are not a language. ISO 639-2 reserves qaa-qtz for local use: broadcasters (DVB, RTVE, 3Cat) use "qaa"
# for the original version and "qad" for audio description; RTVE also uses "ads" for audio description.
ORIGINAL_CODES = {"qaa", "qov"}
AD_CODES = {"ads", "qad"}
NO_LANGUAGE = {"", "und", "zxx", "none", "unknown", "undefined"}
MULTIPLE = {"mul": "Varios idiomas", "mis": "Otro idioma"}

# HLS CHARACTERISTICS (RFC 8216 / Apple): the accessibility roles of a rendition
AD_CHARACTERISTICS = ("public.accessibility.describes-video",)
SDH_CHARACTERISTICS = ("public.accessibility.transcribes-spoken-dialog", "public.accessibility.describes-music-and-sound")

_AD_NAME = re.compile(r"audio ?descrip|audiodeskription|audiodescri|descripcion de audio|\bad\b")
_VO_NAME = re.compile(r"\boriginal\b|\bv\.? ?o\.?$|^vo\b|versio original|version original|\borig\b")
_SDH_NAME = re.compile(r"sordos|\bsdh\b|hearing|subtitulado para|\bsubtitulos para|gehorlos|sourds")
# NAMEs that only repeat an id ("audio_4", "aac1", "stream_3", "output track 01 (PID 306)", "caption_1", "CC1")
_JUNK_NAME = re.compile(r"^(audio|aac|mp4a|stream|track|output track|caption|cc|a|s|sub|subs|subtitle|text)[\s_-]*"
                        r"\d*(\s*\(pid \d+\))?$|^\w*\d\w*$|^[a-z]{2,3}$")
_FFMPEG_TITLES = {"visual impaired", "hearing impaired"}  # added by mpv from the dispositions, not by the channel

BADGES = ("CC", "VO", "AD")


# -- languages ------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _iso639() -> tuple[dict[str, str], dict[str, str]]:
    """(3-letter code -> 2-letter code, 2/3-letter code -> Spanish name) from the system iso-codes; empty without it."""
    for base in ISO_DIRS:
        path = base / "iso-codes" / "json" / "iso_639-2.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["639-2"]
            tr = gettext.translation("iso_639-2", localedir=str(base / "locale"), languages=["es"])
        except (OSError, ValueError, KeyError):
            continue
        to2: dict[str, str] = {}
        names: dict[str, str] = {}
        for r in rows:
            name = tr.gettext(r.get("name") or "")
            # "Español; Castellano" -> "Español"; "Catalán, Valenciano" -> "Catalán"
            name = re.split(r"[;,(]", name, maxsplit=1)[0].strip()
            codes = [r.get("alpha_3"), r.get("bibliographic")]
            two = r.get("alpha_2")
            for c in codes:
                if c and two:
                    to2[c] = two
            for c in [two, *codes]:
                if c and name:
                    names.setdefault(c, name[:1].upper() + name[1:])
        if names:
            return to2, names
    return {}, {}


def lang_code(code: str | None) -> str:
    """"SPA" / "spa" / "es-ES" / "baq" -> "es" / "es" / "es" / "eu"; codes without a 2-letter form stay as they are."""
    c = (code or "").strip().lower().replace("_", "-")
    base = c.split("-", 1)[0]
    if len(base) == 3:
        return _iso639()[0].get(base) or _ISO3_FALLBACK.get(base) or base
    if base == "sp":  # seen in iptv-org lists ("CLOSED-CAPTIONS ... LANGUAGE=sp")
        return "es"
    return base


def is_language(code: str | None) -> bool:
    c = lang_code(code)
    if c in NO_LANGUAGE or c in MULTIPLE or c in ORIGINAL_CODES or c in AD_CODES:
        return False
    if len(c) == 3 and "qaa" <= c <= "qtz":
        return False
    return bool(re.fullmatch(r"[a-z]{2,3}", c))


def language_name(code: str | None) -> str | None:
    """Spanish name of a real language code (None for "qaa", "und"...)."""
    if not is_language(code):
        return None
    c = lang_code(code)
    return LANG_NAMES.get(c) or _iso639()[1].get(c)


# -- normalised tracks ------------------------------------------------------------------------------------------------


def _useful_name(name: str | None, lang: str | None) -> str | None:
    n = (name or "").strip()
    if not n or n.lower() in _FFMPEG_TITLES:
        return None
    key = normalize(n)
    code = (lang or "").lower()
    if not key or key in NO_LANGUAGE or (len(code) <= 3 and key == code) or _JUNK_NAME.match(key):
        return None
    return n


def _track(kind: str, lang: str | None, name: str | None = None, *, characteristics: str = "", forced: bool = False,
           default: bool = False, visual: bool = False, hearing: bool = False, cc: bool = False) -> dict[str, Any]:
    """One track, whatever its origin: {kind, lang, name, role, forced, default, cc}.

    Roles: audio "main" | "vo" (original version) | "ad" (audio description); subtitles "sub" | "sdh" (for the deaf
    and hard of hearing). ``cc``: closed captions carried in the video (CEA-608/708)."""
    code = lang_code(lang)
    key = normalize(name or "")
    chars = characteristics or ""
    role = "main" if kind == "audio" else "sub"
    if kind == "audio":
        if (code in AD_CODES or visual or any(c in chars for c in AD_CHARACTERISTICS) or _AD_NAME.search(key)):
            role = "ad"
        elif code in ORIGINAL_CODES or _VO_NAME.search(key):
            role = "vo"
    elif hearing or any(c in chars for c in SDH_CHARACTERISTICS) or _SDH_NAME.search(key):
        role = "sdh"
    return {"kind": kind, "lang": code, "name": _useful_name(name, code), "role": role, "forced": bool(forced),
            "default": bool(default), "cc": bool(cc)}


def base_label(t: dict[str, Any]) -> str:
    """"Español", "Versión original", "Audiodescripción", "Inglés · para sordos", "Catalán · forzados"."""
    lang = t.get("lang") or ""
    role = t.get("role")
    if role == "ad":
        return "Audiodescripción"
    if role == "vo":
        return "Versión original"
    label = language_name(lang) or MULTIPLE.get(lang) or t.get("name")
    if not label:
        label = "Audio" if t.get("kind") == "audio" else "Subtítulos"
    if role == "sdh":
        label += " · para sordos"
    if t.get("forced"):
        label += " · forzados"
    if t.get("cc"):
        label += " · CC"
    return label


def label_all(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds ``label`` to each track; equal labels of the same kind are told apart by their NAME, or numbered."""
    for t in tracks:
        t["label"] = base_label(t)
    for kind in ("audio", "sub"):
        seen: dict[str, list[dict[str, Any]]] = {}
        for t in tracks:
            if t["kind"] == kind:
                seen.setdefault(t["label"], []).append(t)
        for label, same in seen.items():
            if len(same) < 2:
                continue
            names = [t.get("name") for t in same]
            distinct = all(names) and len({normalize(n or "") for n in names}) == len(names)
            for i, t in enumerate(same, 1):
                if distinct and normalize(t["name"]) != normalize(label):
                    t["label"] = f"{label} · {t['name']}"
                else:
                    t["label"] = f"{label} ({i})" if i > 1 else label
    return tracks


def badges(tracks: list[dict[str, Any]]) -> list[str]:
    """CC: the channel has subtitles; VO: original version audio; AD: audio description."""
    out = set()
    for t in tracks:
        if t["kind"] == "sub":
            out.add("CC")
        elif t["role"] == "vo":
            out.add("VO")
        elif t["role"] == "ad":
            out.add("AD")
    return [b for b in BADGES if b in out]


def summary(tracks: list[dict[str, Any]], source: str) -> dict[str, Any]:
    """What the lists need to know about a channel, stored by mpvd: labels by kind and the badges."""
    label_all(tracks)
    return {
        "audio": [{"lang": t["lang"], "label": t["label"], "role": t["role"]} for t in tracks if t["kind"] == "audio"],
        "subs": [{"lang": t["lang"], "label": t["label"], "role": t["role"]} for t in tracks if t["kind"] == "sub"],
        "badges": badges(tracks), "from": source,
    }


# -- HLS master playlists -----------------------------------------------------------------------------------------------


def parse_master_media(text: str) -> list[dict[str, Any]]:
    """``#EXT-X-MEDIA`` renditions of a master playlist, as tracks (+ ``uri``, ``group``).

    AUDIO and SUBTITLES renditions, and CLOSED-CAPTIONS (carried in the video: they are subtitles with ``cc``).
    Repeated renditions (the same audio in two groups, one per codec) are listed once."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for line in text.splitlines():
        if not line.upper().startswith("#EXT-X-MEDIA:"):
            continue
        a = parse_hls_attrs(line.split(":", 1)[1])
        typ = (a.get("TYPE") or "").upper()
        kind = {"AUDIO": "audio", "SUBTITLES": "sub", "CLOSED-CAPTIONS": "sub"}.get(typ)
        if not kind:
            continue
        t = _track(kind, a.get("LANGUAGE"), a.get("NAME"), characteristics=a.get("CHARACTERISTICS", ""),
                   forced=a.get("FORCED", "").upper() == "YES", default=a.get("DEFAULT", "").upper() == "YES",
                   cc=typ == "CLOSED-CAPTIONS")
        key = (t["kind"], t["lang"], t["role"], t["forced"], t["cc"], normalize(a.get("NAME") or ""))
        if key in seen:
            continue
        seen.add(key)
        t["uri"] = a.get("URI")
        t["group"] = a.get("GROUP-ID")
        out.append(t)
    return out


# -- mpv track-list -------------------------------------------------------------------------------------------------------


def _same_lang(a: str | None, b: str | None) -> bool:
    return lang_code(a) == lang_code(b)


def label_player_tracks(track_list: list[dict[str, Any]], renditions: list[dict[str, Any]] | None = None
                        ) -> list[dict[str, Any]]:
    """mpv ``track-list`` -> the audio and subtitle tracks to offer, labelled, one per real track.

    Each result keeps mpv's ``id`` (for aid/sid), ``selected`` and ``type`` ("audio"/"sub"). HLS repeats the
    muxed audio in every variant (program): only the copy in the program of the selected video (or the selected one)
    is kept. ``renditions`` (from the master playlist) give back the NAMEs FFmpeg drops, matched by kind and
    language in order."""
    video_prog = next((t.get("program-id") for t in track_list if t.get("type") == "video" and t.get("selected")), None)
    pool = [dict(r) for r in (renditions or [])]
    used: set[int] = set()

    def rendition_for(kind: str, lang: str | None) -> dict[str, Any] | None:
        for i, r in enumerate(pool):
            if i not in used and r["kind"] == kind and _same_lang(r.get("lang"), lang) and not r.get("cc"):
                used.add(i)
                return r
        return None

    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for t in track_list:
        kind = t.get("type")
        if kind not in ("audio", "sub") or t.get("image"):
            continue
        codec = (t.get("codec") or "").lower()
        tr = _track(kind, t.get("lang"), t.get("title"), forced=bool(t.get("forced")), default=bool(t.get("default")),
                    visual=bool(t.get("visual-impaired")), hearing=bool(t.get("hearing-impaired")),
                    cc=codec in ("eia_608", "cc_dec", "eia_708"))
        tr.update({"type": kind, "id": t.get("id"), "selected": bool(t.get("selected")),
                   "external": bool(t.get("external")), "program": t.get("program-id")})
        key = (kind, tr["lang"], tr["role"], tr["forced"], tr["cc"], normalize(t.get("title") or ""),
               bool(t.get("external")) and t.get("id"))
        cur = groups.get(key)
        if cur is None:
            groups[key] = tr
            order.append(key)
        elif (tr["selected"] and not cur["selected"]) or (
                not cur["selected"] and cur.get("program") != video_prog and tr.get("program") == video_prog):
            groups[key] = tr
    out = [groups[k] for k in order]
    for tr in out:
        if tr["external"]:
            continue
        r = rendition_for(tr["kind"], tr["lang"])
        if r:
            if not tr.get("name") and r.get("name"):
                tr["name"] = r["name"]
            if tr["role"] in ("main", "sub") and r["role"] not in ("main", "sub"):
                tr["role"] = r["role"]
            tr["forced"] = tr["forced"] or r.get("forced", False)
    # audio first (the main one, the original version, audio description), then subtitles in mpv's order
    out.sort(key=lambda x: (0, _AUDIO_ORDER.get(x["role"], 3)) if x["kind"] == "audio" else (1, 0))
    return label_all(out)


_AUDIO_ORDER = {"main": 0, "vo": 1, "ad": 2}
