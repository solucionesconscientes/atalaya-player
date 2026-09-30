"""File-name parser for the library: movies ("Película (1975)", "Movie.2010.1080p.WEB-DL.x264-GRP") and episodes
("Serie S01E02", "Serie 1x06", "Serie/Temporada 1/01 - Título", "[Grupo] Anime - 05 [1080p]", "Cap.102").

Pure functions, no I/O besides reading the given names. The series title of an episode comes from the file name when it
has one and from the folders otherwise (``Serie/Temporada 1/S01E02.mkv``, one folder per episode). Release tags
(resolution, source, codecs, audio, languages, editions) and the release group are dropped from titles.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv", ".m2ts",
              ".mts", ".vob", ".divx", ".3gp", ".rmvb"}
EXTRA_DIRS = {"extras", "extra", "featurettes", "behind the scenes", "deleted scenes", "trailers", "trailer", "samples",
              "sample", "interviews", "shorts", "scenes", "other", "bonus", "subs", "subtitles", "subtitulos"}

_SEP = r"[\s._\-\[\](){}+,]"
_B = rf"(?:^|(?<={_SEP}))"          # token start
_E = rf"(?=$|{_SEP})"               # token end

_TAG_WORDS = [
    # resolution / quality
    r"\d{3,4}[pi]", r"[48]k", r"uhd", r"fhd", r"hd", r"hq", r"sd",
    # sources and streaming services
    r"web[\s._-]?dl", r"web[\s._-]?rip", r"web", r"hdtv", r"pdtv", r"sdtv", r"dsr", r"tvrip", r"hdtvrip",
    r"blu[\s._-]?ray", r"bd[\s._-]?rip", r"br[\s._-]?rip", r"bd[\s._-]?remux", r"bd", r"remux", r"dvd[\s._-]?rip",
    r"dvd(?:scr|5|9|r)?", r"hd[\s._-]?rip", r"vhs[\s._-]?rip", r"hdcam", r"cam[\s._-]?rip", r"telesync", r"screener",
    r"scr", r"r5", r"amzn", r"nf", r"dsnp", r"hmax", r"atvp", r"hulu", r"pcok", r"itunes",
    # codecs / bit depth
    r"x26[45]", r"h[\s.]?26[45]", r"hevc", r"avc", r"xvid", r"divx", r"av1", r"vp9", r"10[\s.-]?bits?", r"8[\s.-]?bits?",
    r"hi10p?",
    # audio
    r"aac(?:[\s.]?[257]\.[01])?", r"e?ac3(?:[\s.]?[257]\.[01])?", r"e-ac-3", r"dts(?:[\s.-]?hd)?(?:[\s.-]?ma)?(?:[\s.]?[257]\.[01])?",
    r"ddp?(?:[\s.]?[257]\.[01])?", r"dd\+", r"truehd", r"atmos", r"flac", r"mp3", r"opus", r"lpcm", r"[257]\.[01]",
    r"[26]ch", r"dual(?:[\s.-]?audio)?", r"multi(?:[\s.-]?subs?)?",
    # picture
    r"hdr(?:10)?\+?", r"dv", r"dovi", r"dolby[\s.]vision", r"sdr", r"hlg", r"3d", r"h?sbs",
    # languages / subtitles
    r"spanish", r"castellano", r"espa[ñn]ol", r"esp", r"spa", r"eng", r"english", r"latino", r"lat", r"vose", r"vos",
    r"vo", r"subs?", r"subbed", r"dubbed", r"subtitulado", r"french", r"german", r"italian", r"ita", r"jap(?:anese)?",
    # editions / release flags
    r"proper", r"repack", r"rerip", r"extended(?:[\s.]cut)?", r"unrated", r"uncut", r"remastered", r"limited",
    r"internal", r"complete", r"imax", r"directors?[\s.']?s?[\s.]?cut", r"theatrical", r"criterion", r"readnfo", r"hc",
    r"www[\s.][\w-]+[\s.]\w+",
]
_TAG_RX = re.compile(_B + r"(?:" + "|".join(_TAG_WORDS) + r")" + _E, re.IGNORECASE)
_YEAR_RX = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])")
_BRACKET_RX = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_ACRONYM_RX = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}(?:[A-Za-z](?![A-Za-z0-9]))?")

# episode markers, most specific first; groups: season, episode, [last episode]
_SE_RX = [
    re.compile(r"(?i)(?<![a-z0-9])s(\d{1,3})[\s._-]?e(\d{1,4})(?:[\s._-]?(?:-[\s._-]?e?|e)(\d{1,4}))?(?![0-9])"),
    re.compile(r"(?i)(?<![0-9a-z])(\d{1,2})x(\d{1,3})(?:[\s._-]?-[\s._-]?(?:\d{1,2}x)?(\d{1,3}))?(?![0-9])"),
    re.compile(r"(?i)(?<![a-z])(?:temporada|season|temp\.?|series)[\s._-]*(\d{1,2})[\s,._-]*(?:-[\s]*)?"
               r"(?:cap[ií]tulo|episodio|episode|chapter|cap\.?|ep\.?|e)[\s._-]*(\d{1,3})(?![0-9])"),
    re.compile(r"(?i)(?<![a-z])cap[\s._-]*(\d{1,2})(\d{2})(?![0-9])"),
]
_EP_RX = re.compile(r"(?i)(?<![a-z])(?:episodio|episode|cap[ií]tulo|cap|ep|e)[\s._-]*(\d{1,3})(?![0-9])")
_ANIME_RX = re.compile(r"(?<=\S)\s+-\s+(\d{1,3})(?:v\d)?(?=\s|$|\[|\()")
_LEAD_NUM_RX = re.compile(r"^\s*(?:e|ep|episodio|episode|cap[ií]tulo|chapter|cap)?[\s._-]*(\d{1,3})(?![0-9])",
                          re.IGNORECASE)
_SEASON_DIR_RX = re.compile(r"(?i)^(?:season|temporada|temp|series|serie|staffel|saison|s|t)[\s._-]*(\d{1,2})$")
_SPECIALS_DIR_RX = re.compile(r"(?i)^(?:specials?|especiales|extras de la serie|season 0+|temporada 0+)$")
_SAMPLE_RX = re.compile(r"(?i)(?:^|[\W_])sample(?:[\W_]|$)")

_SMALL_WORDS = {"a", "an", "and", "at", "by", "de", "del", "el", "en", "for", "in", "la", "las", "los", "of", "on",
                "or", "the", "to", "un", "una", "y", "e", "o"}


@dataclass(frozen=True)
class MediaName:
    kind: str                     # "movie" | "episode"
    title: str                    # movie title or series title (display form)
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    episode_title: str = ""

    @property
    def group_key(self) -> str:
        """Grouping key: normalised series title for episodes; normalised title + year for movies."""
        if self.kind == "episode":
            return norm(self.title)
        return norm(self.title) + (f" {self.year}" if self.year else "")


def norm(text: str) -> str:
    """Lower-case ASCII words without accents or punctuation (grouping and search)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def is_video(name: str) -> bool:
    p = PurePath(name)
    return p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")


def is_sample(stem: str) -> bool:
    return bool(_SAMPLE_RX.search(stem))


def _max_year() -> int:
    return _dt.date.today().year + 1


def _pretty(text: str) -> str:
    """Separators to spaces (keeping acronyms like S.W.A.T.), trimmed punctuation, lower-case names capitalised."""
    src = text
    text = _ACRONYM_RX.sub(lambda m: m.group(0).replace(".", "\x00") + (
        " " if m.group(0).endswith(".") and m.end() < len(src) and src[m.end()].isalnum() else ""), text)
    text = re.sub(r"_+|\.(?! )", " ", text).replace("\x00", ".")
    text = re.sub(r"[()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:,;+&'\"")
    if text and text == text.lower() and any(c.isalpha() for c in text):
        words = text.split(" ")
        text = " ".join(w if (i and w in _SMALL_WORDS) else (w[:1].upper() + w[1:]) for i, w in enumerate(words))
    return text


def _cut_tags(text: str) -> str:
    """Text before the first release tag (the whole text when there is none)."""
    m = _TAG_RX.search(text)
    return text[:m.start()] if m else text


def split_year(text: str) -> tuple[str, int | None]:
    """``"Blade Runner 2049 (2017)"`` → (``"Blade Runner 2049 "``, 2017): the last plausible year that is not the
    first word (``"1917"`` and ``"2001 A Space Odyssey"`` keep their number)."""
    best = None
    for m in _YEAR_RX.finditer(text):
        y = int(m.group(1))
        if y > _max_year():
            continue
        before = text[:m.start()]
        if not re.search(r"[^\W_]", before):
            continue       # the title itself starts with the number
        best = m
    if best is None:
        return text, None
    return text[:best.start()], int(best.group(1))


def clean_title(text: str) -> tuple[str, int | None]:
    """Display title and year of a movie-like name (brackets, tags, release group and year removed)."""
    text = _BRACKET_RX.sub(" ", text)
    text = _cut_tags(text)
    text, year = split_year(text)
    title = _pretty(text)
    return title, year


def _episode_title(text: str) -> str:
    text = _BRACKET_RX.sub(" ", text)
    text = _cut_tags(text)
    return _pretty(text)


@dataclass(frozen=True)
class _Marker:
    start: int
    end: int
    season: int | None
    episode: int
    episode_end: int | None


def find_marker(name: str) -> _Marker | None:
    """First episode marker in a file or folder name."""
    for rx in _SE_RX:
        m = rx.search(name)
        if m:
            season, ep = int(m.group(1)), int(m.group(2))
            last = int(m.group(3)) if m.lastindex and m.lastindex >= 3 and m.group(3) else None
            if last is not None and last <= ep:
                last = None
            return _Marker(m.start(), m.end(), season, ep, last)
    m = _EP_RX.search(name)
    if m:
        return _Marker(m.start(), m.end(), None, int(m.group(1)), None)
    clean = _BRACKET_RX.sub(lambda b: " " * len(b.group(0)), name)
    m = _ANIME_RX.search(clean)
    if m:
        return _Marker(m.start(), m.end(), None, int(m.group(1)), None)
    return None


def season_dir(name: str) -> int | None:
    """Season number of a folder called ``Season 1``, ``Temporada 01``, ``S01``, ``Specials`` (0)…"""
    n = name.strip()
    if _SPECIALS_DIR_RX.match(n):
        return 0
    m = _SEASON_DIR_RX.match(n.replace("_", " ").replace(".", " "))
    return int(m.group(1)) if m else None


def folder_season(name: str) -> int | None:
    """Like ``season_dir`` but also ``Serie Temporada 2`` / ``Show Season 2`` (season at the end of the name)."""
    s = season_dir(name)
    if s is not None:
        return s
    m = re.search(r"(?i)(?<![a-z])(?:temporada|season|temp)[\s._-]*(\d{1,2})\s*$", name.strip())
    return int(m.group(1)) if m else None


def _series_from_dirs(parents: list[str]) -> tuple[str, int | None, int | None]:
    """(series title, year, season) from the folders above an episode (nearest first)."""
    season = None
    for d in parents[:3]:
        s = season_dir(d)
        if s is not None:
            season = s
            continue
        mk = find_marker(d)
        if mk is not None:       # one folder per episode ("Serie 1x03") or "Serie Temporada 2"
            title, year = clean_title(d[:mk.start])
            if title:
                return title, year, mk.season if mk.season is not None else season
            continue
        m = re.search(r"(?i)(?:temporada|season|temp)[\s._-]*(\d{1,2})\s*$", _pretty(d))
        if m:
            title, year = clean_title(_pretty(d)[:m.start()])
            return title, year, int(m.group(1)) if season is None else season
        title, year = clean_title(d)
        if title:
            return title, year, season
    return "", None, season


def parse_path(path: str | PurePath, root: str | PurePath | None = None) -> MediaName:
    """Classify one video file. ``root`` (the library folder) is never used as a series or movie name."""
    p = PurePath(path)
    stem = p.stem
    parents: list[str] = []
    root_p = PurePath(root) if root is not None else None
    for parent in p.parents:
        if root_p is not None and (parent == root_p or len(parent.parts) <= len(root_p.parts)):
            break
        if parent.name:
            parents.append(parent.name)
    parents = [d for d in parents if d.lower() not in EXTRA_DIRS] or parents

    mk = find_marker(stem)
    dir_season = folder_season(parents[0]) if parents else None
    if mk is None and dir_season is not None:
        # "Serie/Temporada 1/01 - Piloto.mkv": the leading number is the episode (none: ordered by name)
        lead = _LEAD_NUM_RX.match(stem)
        s_title, s_year, _s = _series_from_dirs(parents)
        if lead:
            return MediaName("episode", s_title or _pretty(stem), s_year, dir_season, int(lead.group(1)), None,
                             _episode_title(stem[lead.end(1):]))
        return MediaName("episode", s_title or _pretty(stem), s_year, dir_season, None, None, _episode_title(stem))
    if mk is None and parents:
        folder_mk = find_marker(parents[0])
        if folder_mk is not None:      # one folder per episode: "Serie 1x03/video.mp4"
            title, year = clean_title(parents[0][:folder_mk.start])
            if not title:
                title, year, _s = _series_from_dirs(parents[1:])
            season = folder_mk.season if folder_mk.season is not None else 1
            return MediaName("episode", title or _pretty(parents[0]), year, season, folder_mk.episode,
                             folder_mk.episode_end, "")
    if mk is not None and mk.season is None and dir_season is None:
        # "Star Wars Episode 4 A New Hope (1977)": an episode-only marker followed by a year is a movie
        if re.search(r"\((?:19|20)\d{2}\)", stem[mk.end:]):
            mk = None
    if mk is not None:
        before = stem[:mk.start]
        title, year = clean_title(before)
        season = mk.season
        d_title, d_year, d_season = _series_from_dirs(parents)
        if not title:
            title, year = d_title, d_year
        if season is None:
            season = d_season if d_season is not None else 1
        ep_title = _episode_title(stem[mk.end:])
        if ep_title and norm(ep_title) == norm(title):
            ep_title = ""
        return MediaName("episode", title or _pretty(stem), year, season, mk.episode, mk.episode_end, ep_title)

    title, year = clean_title(stem)
    if year is None and parents:
        f_title, f_year = clean_title(parents[0])
        if f_year is not None and f_title:
            return MediaName("movie", f_title, f_year)
    if not title and parents:
        title, year = clean_title(parents[0])
    return MediaName("movie", title or stem, year)
