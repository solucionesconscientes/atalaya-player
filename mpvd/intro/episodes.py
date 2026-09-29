"""Which files are "the other episodes" of a video: same folder first, then sibling folders (one folder per episode, as
many downloads come), matched by normalised series title + season parsed from the file or folder name.

Recognised markers (case-insensitive): ``S01E02`` / ``s1.e2``, ``1x02``, ``Temporada 1 Capítulo 2`` (also
``Season 1 Episode 2``), ``Cap.102`` (Spanish scene style: season 1, episode 02) and, without a season, ``Ep 3`` /
``E03`` / ``Episodio 3`` / ``Capítulo 3``. The title is what comes before the marker, without bracketed tags, accents
or punctuation (``El Padre Brown [HDTV][Cap.1x02]`` → ``el padre brown``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv", ".m2ts"}
MAX_SCAN_DIRS = 400          # sibling folders inspected at most (a huge Downloads folder must stay cheap)

_SE = [
    re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[\s._-]*e(\d{1,3})(?!\d)"),
    re.compile(r"(?i)(?<![0-9])(\d{1,2})x(\d{1,3})(?![0-9])"),
    re.compile(r"(?i)(?:temporada|season|temp\.?)[\s._-]*(\d{1,2})[\s,._-]*(?:-[\s]*)?"
               r"(?:cap[ií]tulo|episodio|episode|cap\.?|ep\.?)[\s._-]*(\d{1,3})(?!\d)"),
    re.compile(r"(?i)(?<![a-z])cap[\s._-]*(\d)(\d{2})(?!\d)"),
]
_EP_ONLY = re.compile(r"(?i)(?<![a-z])(?:episodio|episode|cap[ií]tulo|chapter|cap|ep|e)[\s._-]*(\d{1,3})(?!\d)")
_ANY_NUM = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_BRACKETS = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
_TAIL_WORDS = {"cap", "capitulo", "episodio", "episode", "ep", "e", "temporada", "season", "temp", "t", "s", "x", "chapter"}


@dataclass(frozen=True)
class EpisodeInfo:
    title: str                  # normalised series title ("" when unknown)
    season: int | None
    episode: int | None

    @property
    def known(self) -> bool:
        return bool(self.title) and self.episode is not None


def norm_title(text: str) -> str:
    """Lower-case ASCII words: no accents, punctuation, bracketed tags or trailing marker words."""
    text = _BRACKETS.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    while words:
        if words[-1] in _TAIL_WORDS:
            words.pop()
        elif len(words) >= 2 and words[-1].isdigit() and words[-2] in ("temporada", "season", "temp"):
            del words[-2:]
        else:
            break
    return " ".join(words)


def parse_episode(name: str) -> EpisodeInfo | None:
    """Series title, season and episode from a file stem or folder name (None when no marker is found)."""
    for rx in _SE:
        m = rx.search(name)
        if m:
            return EpisodeInfo(norm_title(name[:m.start()]), int(m.group(1)), int(m.group(2)))
    m = _EP_ONLY.search(name)
    if m:
        return EpisodeInfo(norm_title(name[:m.start()]), None, int(m.group(1)))
    return None


def episode_info(path: Path) -> EpisodeInfo:
    """From the file name, completed with the folder name (``Serie 1x03/video.mp4``)."""
    mine = parse_episode(path.stem)
    folder = parse_episode(path.parent.name)
    if mine is None:
        return folder or EpisodeInfo("", None, None)
    if not mine.title and folder is not None and folder.title:
        season = mine.season if mine.season is not None else folder.season
        return EpisodeInfo(folder.title, season, mine.episode)
    return mine


def episode_number(path: Path) -> int:
    """Episode number used to sort neighbours (last number of the name as a fallback, huge when there is none)."""
    info = episode_info(path)
    if info.episode is not None:
        return info.episode
    nums = _ANY_NUM.findall(path.stem)
    return int(nums[-1]) if nums else 10**6


def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")


def _videos(folder: Path) -> list[Path]:
    try:
        return [p for p in folder.iterdir() if is_video(p) and p.is_file()]
    except OSError:
        return []


def _same_season(info: EpisodeInfo, other: Path) -> bool:
    o = episode_info(other)
    return o.episode is not None and o.title == info.title and o.season == info.season


def season_episodes(path: Path, max_dirs: int = MAX_SCAN_DIRS) -> list[Path]:
    """Every other episode of the same season, nearest episode numbers first.

    1. Videos of the same folder with the same title and season.
    2. Otherwise, videos one level down in the sibling folders (and loose in the parent) with the same title and season.
    3. Otherwise (no recognisable title: ``ep01.mkv``, ``master.mp4``…), every other video of the same folder."""
    if not is_video(path) or not path.parent.is_dir():
        return []
    same = [p for p in _videos(path.parent) if p != path]
    info = episode_info(path)
    found: list[Path] = []
    if info.known:
        found = [p for p in same if _same_season(info, p)]
        if not found:
            found = _sibling_folders(path, info, max_dirs)
    if not found:
        found = same
    mine = episode_number(path)
    found.sort(key=lambda p: (abs(episode_number(p) - mine), episode_number(p) < mine, p.name))
    return found


def _sibling_folders(path: Path, info: EpisodeInfo, max_dirs: int) -> list[Path]:
    parent = path.parent.parent
    if parent == path.parent:
        return []
    out: list[Path] = []
    try:
        entries = sorted(parent.iterdir())
    except OSError:
        return []
    scanned = 0
    for entry in entries:
        if entry.name.startswith(".") or entry == path.parent:
            continue
        try:
            if entry.is_dir():
                scanned += 1
                if scanned > max_dirs:
                    break
                out.extend(p for p in _videos(entry) if _same_season(info, p))
            elif is_video(entry) and entry.is_file() and _same_season(info, entry):
                out.append(entry)
        except OSError:
            continue
    return out


def siblings(path: Path, limit: int = 3) -> list[Path]:
    """The ``limit`` nearest episodes (see ``season_episodes``)."""
    return season_episodes(path)[:limit]


def next_episode(path: Path, episodes: list[Path] | None = None) -> Path | None:
    """The following episode of the same season (lowest episode number above this one), if any."""
    info = episode_info(path)
    if info.episode is None:
        return None
    later = [p for p in (episodes if episodes is not None else season_episodes(path))
             if episode_info(p).episode is not None and episode_info(p).episode > info.episode]  # type: ignore[operator]
    if not later:
        return None
    return min(later, key=lambda p: (episode_info(p).episode, p.name))
