"""TV guide (EPG) from the XMLTV file a channel list announces (``#EXTM3U url-tvg="…"``).

TDTChannels (verified 2026-09-30): ``https://www.tdtchannels.com/epg/TV.xml.gz``, ~540 KB gzip / 4.7 MB XML, 184
``<channel id="La1.TV">`` (display-name = the id), ~11 000 ``<programme channel=… start="20260930055000 +0000"
stop=…>`` with ``title``/``desc``/``icon``/``category``, four days ahead, times in UTC. The list's ``tvg-id`` is
the same string, but two thirds of its entries carry none: those match by normalized name ("La 1" ~ "La1.TV").

Downloaded through the HTTP cache (ETag, TTL 12 h), parsed in streaming (``iterparse``, elements cleared as they
go) into SQLite in ``cache_dir`` (``epg.sqlite3``), one import per EPG URL. Refresh runs as a low-priority job,
never more often than every ~12 h unless forced; queries never wait for it.
"""

from __future__ import annotations

import asyncio
import calendar
import gzip
import logging
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from mpvd.iptv.index import normalize
from mpvd.jobs import Priority
from mpvd.net import FetchError, HttpCache
from mpvd.rpc import INVALID_PARAMS, RpcError

if TYPE_CHECKING:
    from mpvd.iptv.model import Channel
    from mpvd.iptv.service import IptvService
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.iptv.epg")

EPG_TTL = 12 * 3600.0
KEEP_PAST = 6 * 3600.0  # programmes that ended longer ago are not imported
SCHEMA_VERSION = 1
NOTIFY = "mu_iptv"

_TIME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?\s*(?:([+-])(\d{2}):?(\d{2}))?")
_SUFFIX_RE = re.compile(r"(@[^@]*)$|\.(tv|radio|[a-z]{2})$", re.IGNORECASE)


def parse_time(value: str | None) -> float | None:
    """XMLTV date ``YYYYMMDDhhmmss +hhmm`` (seconds, minutes and offset optional) -> epoch seconds.

    Without an offset the time is taken as UTC (what the XMLTV DTD says)."""
    m = _TIME_RE.match((value or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, sign, oh, om = m.groups()
    try:
        ts = calendar.timegm((int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), 0, 0, 0))
    except (ValueError, OverflowError):
        return None
    if sign:
        offset = int(oh) * 3600 + int(om) * 60
        ts -= offset if sign == "+" else -offset
    return float(ts)


def name_key(text: str | None) -> str:
    """Channel name/id reduced for matching: no accents, case, spaces or punctuation ("La 1" == "La1.TV")."""
    return re.sub(r"[^0-9a-z]", "", normalize(text or ""))


def id_key(epg_id: str) -> str:
    """An EPG id as a name: without the list's type suffix (".TV", ".Radio", iptv-org ".es@SD")."""
    base = epg_id
    for _ in range(2):
        base = _SUFFIX_RE.sub("", base)
    return name_key(base)


@dataclass
class Programme:
    channel: str
    start: float
    stop: float
    title: str
    desc: str = ""
    category: str = ""
    icon: str = ""

    def to_dict(self, with_desc: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {"title": self.title, "start": self.start, "stop": self.stop}
        if self.category:
            d["category"] = self.category
        if with_desc:
            if self.desc:
                d["desc"] = self.desc
            if self.icon:
                d["icon"] = self.icon
        return d


def _open_xml(path: Path) -> IO[bytes]:
    with open(path, "rb") as fh:
        magic = fh.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def iter_xmltv(stream: IO[bytes]) -> Iterator[tuple[str, Any]]:
    """Streaming XMLTV reader: yields ("channel", (id, [display names])) and ("programme", Programme)."""
    context = ET.iterparse(stream, events=("start", "end"))
    root = None
    for event, elem in context:
        if event == "start":
            if root is None:
                root = elem
            continue
        if elem.tag == "channel":
            cid = elem.get("id") or ""
            names = [(n.text or "").strip() for n in elem.findall("display-name") if (n.text or "").strip()]
            if cid:
                yield "channel", (cid, names)
            if root is not None:
                root.clear()
        elif elem.tag == "programme":
            start = parse_time(elem.get("start"))
            stop = parse_time(elem.get("stop"))
            cid = elem.get("channel") or ""
            title = (elem.findtext("title") or "").strip()
            if cid and start is not None and title:
                icon = elem.find("icon")
                yield "programme", Programme(
                    channel=cid, start=start, stop=stop if stop is not None and stop > start else start,
                    title=title, desc=(elem.findtext("desc") or "").strip(),
                    category=(elem.findtext("category") or "").strip(),
                    icon=(icon.get("src") or "") if icon is not None else "")
            if root is not None:
                root.clear()  # keep memory flat: drop everything parsed so far


def fill_stops(progs: list[Programme]) -> None:
    """Programmes without ``stop`` end where the next one of the same channel starts (list sorted by start)."""
    for cur, nxt in zip(progs, progs[1:]):
        if cur.stop <= cur.start:
            cur.stop = nxt.start
    if progs and progs[-1].stop <= progs[-1].start:
        progs[-1].stop = progs[-1].start + 3600


class EpgStore:
    """SQLite: channels and programmes per EPG URL (``src``); a match table (id / name key -> EPG id) in memory."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (src TEXT PRIMARY KEY, imported_at REAL, fetched_at REAL,
                    channels INTEGER, programmes INTEGER, first REAL, last REAL, schema INTEGER);
                CREATE TABLE IF NOT EXISTS channels (src TEXT, epg_id TEXT, names TEXT, PRIMARY KEY (src, epg_id));
                CREATE TABLE IF NOT EXISTS programmes (src TEXT, channel TEXT, start REAL, stop REAL, title TEXT,
                    desc TEXT, category TEXT, icon TEXT);
                CREATE INDEX IF NOT EXISTS programmes_by_channel ON programmes (src, channel, stop);
            """)
            self.db.execute("DELETE FROM meta WHERE schema IS NOT ? ", (SCHEMA_VERSION,))
            self.db.commit()
        self._keys: dict[str, tuple[dict[str, str], dict[str, str]]] = {}  # src -> (lower id -> id, name key -> id)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def meta(self, src: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT imported_at, fetched_at, channels, programmes, first, last FROM meta "
                                  "WHERE src=?", (src,)).fetchone()
        if not row:
            return None
        return dict(zip(("imported_at", "fetched_at", "channels", "programmes", "first", "last"), row))

    def touch(self, src: str, fetched_at: float) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE meta SET imported_at=?, fetched_at=? WHERE src=?", (time.time(), fetched_at, src))

    def import_file(self, src: str, path: Path, fetched_at: float = 0.0, now: float | None = None) -> dict[str, Any]:
        """Replace the guide of ``src`` with the XMLTV file at ``path`` (plain or gzip), in one transaction."""
        now = time.time() if now is None else now
        chans: list[tuple[str, str, str]] = []
        by_channel: dict[str, list[Programme]] = {}
        with _open_xml(path) as stream:
            for kind, item in iter_xmltv(stream):
                if kind == "channel":
                    cid, names = item
                    chans.append((src, cid, "\n".join(names)))
                else:
                    by_channel.setdefault(item.channel, []).append(item)
        rows = []
        first = last = None
        for progs in by_channel.values():
            progs.sort(key=lambda p: p.start)
            fill_stops(progs)
            for p in progs:
                if p.stop < now - KEEP_PAST:
                    continue
                rows.append((src, p.channel, p.start, p.stop, p.title, p.desc, p.category, p.icon))
                first = p.start if first is None else min(first, p.start)
                last = p.stop if last is None else max(last, p.stop)
        known = {c[1] for c in chans}
        chans += [(src, cid, "") for cid in by_channel if cid not in known]  # programmes of undeclared channels
        with self.lock:
            with self.db:
                self.db.execute("DELETE FROM channels WHERE src=?", (src,))
                self.db.execute("DELETE FROM programmes WHERE src=?", (src,))
                self.db.executemany("INSERT OR REPLACE INTO channels VALUES (?,?,?)", chans)
                self.db.executemany("INSERT INTO programmes VALUES (?,?,?,?,?,?,?,?)", rows)
                self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?,?,?,?)",
                                (src, time.time(), fetched_at, len(chans), len(rows), first, last, SCHEMA_VERSION))
            self._keys.pop(src, None)
        return {"channels": len(chans), "programmes": len(rows), "first": first, "last": last}

    # -- matching -----------------------------------------------------------------------------

    def _match_tables(self, src: str) -> tuple[dict[str, str], dict[str, str]]:
        tables = self._keys.get(src)
        if tables is None:
            by_id: dict[str, str] = {}
            by_name: dict[str, str] = {}
            with self.lock:
                rows = self.db.execute("SELECT epg_id, names FROM channels WHERE src=?", (src,)).fetchall()
            for epg_id, names in rows:
                by_id[epg_id.lower()] = epg_id
                by_name.setdefault(id_key(epg_id), epg_id)
                for n in (names or "").split("\n"):
                    if n and n != epg_id:
                        by_name.setdefault(name_key(n), epg_id)
            by_name.pop("", None)
            tables = (by_id, by_name)
            self._keys[src] = tables
        return tables

    def match(self, src: str, tvg_id: str | None, name: str | None) -> str | None:
        """EPG channel id for a list entry: its ``tvg-id`` when the guide has it, else its name without accents."""
        by_id, by_name = self._match_tables(src)
        if tvg_id and tvg_id.lower() in by_id:
            return by_id[tvg_id.lower()]
        key = name_key(name)
        if key and key in by_name:
            return by_name[key]
        if tvg_id:  # a tvg-id unknown to this guide may still name the channel ("Canal.TV" vs "canal")
            key = id_key(tvg_id)
            return by_name.get(key) if key else None
        return None

    # -- queries ------------------------------------------------------------------------------

    def _rows(self, sql: str, args: tuple[Any, ...]) -> list[Programme]:
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [Programme(*r) for r in rows]

    def now_next(self, src: str, epg_id: str, at: float) -> tuple[Programme | None, Programme | None]:
        progs = self._rows("SELECT channel, start, stop, title, desc, category, icon FROM programmes "
                           "WHERE src=? AND channel=? AND stop>? ORDER BY start LIMIT 2", (src, epg_id, at))
        if not progs:
            return None, None
        if progs[0].start <= at:
            return progs[0], progs[1] if len(progs) > 1 else None
        return None, progs[0]

    def grid(self, src: str, epg_id: str, start: float, stop: float) -> list[Programme]:
        return self._rows("SELECT channel, start, stop, title, desc, category, icon FROM programmes "
                          "WHERE src=? AND channel=? AND stop>? AND start<? ORDER BY start", (src, epg_id, start, stop))


class EpgService:
    """Which EPG belongs to a channel (its source's url-tvg), background refresh and the queries mu-iptv needs."""

    def __init__(self, server: MpvdServer, iptv: IptvService, http: HttpCache | None = None):
        self.server = server
        self.iptv = iptv
        self.http = http or iptv.http
        self.store = EpgStore(server.settings.cache_dir / "epg.sqlite3")
        self._jobs: dict[str, Any] = {}  # url -> running Job
        self._errors: dict[str, str] = {}

    def close(self) -> None:
        self.store.close()

    # -- sources ------------------------------------------------------------------------------

    def url_of(self, source_id: str) -> str | None:
        st = self.iptv._states.get(source_id)
        return st.epg_url if st and st.epg_url and st.epg_url.lower().startswith(("http://", "https://", "file:"))\
            else None

    def urls(self) -> list[str]:
        return sorted({u for u in (self.url_of(s) for s in list(self.iptv._states)) if u})

    def fresh(self, url: str) -> bool:
        meta = self.store.meta(url)
        return bool(meta) and time.time() - float(meta["imported_at"] or 0) < EPG_TTL

    def _refresh_sync(self, url: str, force: bool) -> dict[str, Any]:
        if url.startswith("file:"):
            path = Path(unquote(urlsplit(url).path))
            fetched_at = path.stat().st_mtime
        else:
            res = self.http.fetch(url, ttl=EPG_TTL, force=force, timeout=120)
            path, fetched_at = res.path, res.fetched_at
            meta = self.store.meta(url)
            # the cached file did not change since the last import (304 or still within its TTL): nothing to parse
            if meta and not force and res.from_cache and meta.get("fetched_at") and \
                    abs(float(meta["fetched_at"]) - fetched_at) < 1:
                return {"url": url, "unchanged": True, **meta}
            if meta and res.status == 304:  # same file as the one imported: only its age changes
                self.store.touch(url, fetched_at)
                return {"url": url, "unchanged": True, **meta}
        t0 = time.monotonic()
        info = self.store.import_file(url, path, fetched_at=fetched_at)
        log.info("epg %s: %d channels, %d programmes in %.1fs", url, info["channels"], info["programmes"],
                 time.monotonic() - t0)
        return {"url": url, **info}

    def refresh(self, url: str, force: bool = False) -> Any:
        """Refresh one EPG in a low-priority job (deduplicated); returns the job."""
        job = self._jobs.get(url)
        if job is not None and job.status.value in ("queued", "running"):
            return job

        async def body(job: Any) -> dict[str, Any]:
            try:
                out = await asyncio.to_thread(self._refresh_sync, url, force)
            except (FetchError, ET.ParseError, OSError, EOFError, sqlite3.Error) as exc:
                self._errors[url] = str(exc)
                log.warning("epg %s: %s", url, exc)
                raise
            self._errors.pop(url, None)
            self._push({"event": "epg", "url": url, "programmes": out.get("programmes", 0)})
            return out

        job = self.server.jobs.submit("iptv.epg.refresh", body, priority=Priority.INDEX, heavy=False,
                                      meta={"url": url})
        self._jobs[url] = job
        return job

    def ensure(self, url: str) -> bool:
        """True when the guide can be queried now; starts a background refresh when it is missing or old."""
        if not self.fresh(url) and url not in self._errors:
            self.refresh(url)
        elif url in self._errors and not self.fresh(url):
            # retry a failed download at most every 10 minutes
            job = self._jobs.get(url)
            if job is None or (job.finished_at and time.time() - job.finished_at > 600):
                self._errors.pop(url, None)
                self.refresh(url)
        return self.store.meta(url) is not None

    def loading(self, url: str) -> bool:
        job = self._jobs.get(url)
        return job is not None and job.status.value in ("queued", "running")

    def _push(self, payload: dict[str, Any]) -> None:
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(NOTIFY, "epg:" + str(payload.get("url")), "done", payload, final=True)

    # -- queries ------------------------------------------------------------------------------

    def prepare(self, ids: list[str]) -> tuple[list[Channel], bool]:
        """In the event loop: the channels behind ``ids`` (unknown ids skipped) and whether one of their guides is
        being downloaded; starts the refresh of the guides that are missing or old (jobs are not thread-safe)."""
        chans: list[Channel] = []
        urls: set[str] = set()
        for cid in ids:
            try:
                ch = self.iptv.get(cid)
            except RpcError:
                continue
            chans.append(ch)
            url = self.url_of(ch.source)
            if url:
                urls.add(url)
        for url in urls:
            self.ensure(url)
        return chans, any(self.loading(u) for u in urls)

    def locate(self, ch: Channel) -> tuple[str | None, str | None]:
        """(EPG url, EPG channel id) of a channel, or (url, None) when its guide does not list it."""
        url = self.url_of(ch.source)
        if not url:
            return None, None
        return url, self.store.match(url, ch.tvg_id, ch.name)

    def now(self, chans: list[Channel], at: float | None = None) -> dict[str, Any]:
        """{channel id: {now, next}} (SQLite only: runs in a worker thread)."""
        at = time.time() if at is None else at
        out: dict[str, Any] = {}
        for ch in chans:
            url, epg_id = self.locate(ch)
            if not url or not epg_id:
                continue
            cur, nxt = self.store.now_next(url, epg_id, at)
            if cur is None and nxt is None:
                continue
            out[ch.id] = {"now": cur.to_dict(False) if cur else None, "next": nxt.to_dict(False) if nxt else None}
        return out

    def channel(self, ch: Channel, start: float, hours: float = 24.0) -> dict[str, Any]:
        url, epg_id = self.locate(ch)
        progs = self.store.grid(url, epg_id, start, start + hours * 3600) if url and epg_id else []
        return {"channel": {"id": ch.id, "name": ch.name, "kind": ch.kind}, "epg_id": epg_id, "at": start,
                "has_guide": bool(url), "programmes": [p.to_dict() | {"now": p.start <= start < p.stop} for p in progs]}

    def status(self) -> list[dict[str, Any]]:
        return [{"url": u, "meta": self.store.meta(u), "loading": self.loading(u), "error": self._errors.get(u)}
                for u in self.urls()]


def register(server: MpvdServer, service: EpgService) -> None:
    d = server.dispatcher

    @d.method("iptv.epg.now")
    async def now(ctx: RpcContext, ids: list[str], at: float | None = None) -> dict[str, Any]:
        """What is on now and next for several channels at once ({channels: {id: {now, next}}, loading})."""
        if not isinstance(ids, list):
            raise RpcError(INVALID_PARAMS, "ids must be a list of channel ids")
        await service.iptv.ensure_loaded()
        at = time.time() if at is None else float(at)
        chans, loading = service.prepare([str(i) for i in ids[:2000]])
        found = await asyncio.to_thread(service.now, chans, at)
        return {"at": at, "channels": found, "loading": loading}

    @d.method("iptv.epg.channel")
    async def channel(ctx: RpcContext, id: str, start: float | None = None, hours: float = 24.0) -> dict[str, Any]:  # noqa: A002
        """Guide of one channel from ``start`` (default now) for ``hours`` (programmes with title, desc, times)."""
        await service.iptv.ensure_loaded()
        ch = service.iptv.get(id)
        _, loading = service.prepare([id])
        start = time.time() if start is None else float(start)
        result = await asyncio.to_thread(service.channel, ch, start, max(1.0, min(float(hours), 96.0)))
        result["loading"] = loading
        sched = getattr(server, "schedule", None)
        if sched is not None:
            for p in result["programmes"]:
                p["scheduled"] = sched.covers(id, p["start"], p["stop"])
        return result

    @d.method("iptv.epg.refresh")
    async def refresh(ctx: RpcContext, force: bool = False, wait: bool = False) -> dict[str, Any]:
        """Download and import the guides of the loaded lists (background, low priority; ``wait`` for the result)."""
        await service.iptv.ensure_loaded()
        jobs = [service.refresh(u, force=force) for u in service.urls()]
        if wait:
            for job in jobs:
                try:
                    await job.wait()
                except Exception:  # noqa: BLE001 - reported in status below
                    pass
        return {"jobs": [j.to_dict() for j in jobs], "guides": service.status()}
