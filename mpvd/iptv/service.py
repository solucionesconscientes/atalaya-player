"""IPTV service: sources, catalogue, facets, search, playback options, favourites/recents, health checks.

Exposed as ``iptv.*`` JSON-RPC methods (see ``register``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections import Counter
from typing import TYPE_CHECKING, Any

from mpvd.iptv.index import SearchIndex
from mpvd.iptv.model import Channel
from mpvd.iptv.radiobrowser import SOURCE_ID as RADIO_SOURCE, RadioBrowser
from mpvd.iptv.sources import BUILTIN_SOURCES, Source, SourceState, load_source
from mpvd.iptv.store import IptvStore
from mpvd.jobs import Job
from mpvd.net import FetchError, HttpCache
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.iptv")

FACETS = ("group", "country", "category", "language")
COUNTRIES_URL = "https://iptv-org.github.io/api/countries.json"  # [{name, code, flag, languages}]


def _sources_from_env() -> list[Source] | None:
    """MPV_UOS_IPTV_SOURCES=<json file> replaces the built-in sources (tests, offline mirrors)."""
    import os  # noqa: PLC0415

    path = os.environ.get("MPV_UOS_IPTV_SOURCES")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        return [Source(**row) for row in rows]
    except (OSError, ValueError, TypeError) as exc:
        log.warning("ignoring MPV_UOS_IPTV_SOURCES (%s): %s", path, exc)
        return None


class IptvService:
    def __init__(self, server: MpvdServer, sources: list[Source] | None = None, radio_base_url: str | None = None):
        self.server = server
        settings = server.settings
        self.http = HttpCache(settings.cache_dir / "http")
        self.store = IptvStore(settings.data_dir / "iptv.sqlite3")
        if sources is None:
            sources = _sources_from_env() or BUILTIN_SOURCES
        self._builtin = list(sources)
        self._states: dict[str, SourceState] = {}
        self._index = SearchIndex()
        self._by_id: dict[str, Channel] = {}
        self._loads: dict[str, asyncio.Task[SourceState]] = {}
        self._lock = asyncio.Lock()
        import os  # noqa: PLC0415

        self.radio = RadioBrowser(self.http, base_url=radio_base_url or os.environ.get("MPV_UOS_RADIO_BROWSER_URL"))
        self.countries_url = os.environ.get("MPV_UOS_COUNTRIES_URL", COUNTRIES_URL)
        self._country_names: dict[str, dict[str, str]] | None = None

    # -- sources ------------------------------------------------------------------------------

    def sources(self) -> list[Source]:
        user = [Source(s["id"], s["name"], s["url"], s["kind"], "user", builtin=False)
                for s in self.store.sources() if s["enabled"]]
        return self._builtin + user

    def source(self, source_id: str) -> Source:
        for s in self.sources():
            if s.id == source_id:
                return s
        raise RpcError(NOT_FOUND, f"unknown source: {source_id}")

    async def load(self, source_id: str, force: bool = False) -> SourceState:
        """Load one source (deduplicating concurrent loads); results are cached in memory."""
        source = self.source(source_id)
        task = self._loads.get(source_id)
        if task is None or task.done():
            task = asyncio.create_task(asyncio.to_thread(load_source, self.http, source, force))
            self._loads[source_id] = task
        state = await task
        async with self._lock:
            if not state.error or source_id not in self._states:
                self._states[source_id] = state
            self._rebuild_index()
        return self._states[source_id]

    async def load_all(self, force: bool = False, only: list[str] | None = None) -> list[SourceState]:
        ids = only or [s.id for s in self.sources()]
        return list(await asyncio.gather(*(self.load(sid, force) for sid in ids)))

    def _rebuild_index(self) -> None:
        self._index = SearchIndex()
        self._by_id = {}
        for state in self._states.values():
            for ch in state.channels:
                self._by_id[ch.id] = ch
                self._index.add(ch)

    async def ensure_loaded(self, source_id: str | None = None) -> None:
        if source_id is not None:
            if source_id not in self._states:
                await self.load(source_id)
            return
        missing = [s.id for s in self.sources() if s.id not in self._states]
        if missing:
            await self.load_all(only=missing)

    # -- catalogue ----------------------------------------------------------------------------

    def channels_of(self, source_id: str | None = None) -> list[Channel]:
        if source_id is None:
            return [ch for st in self._states.values() for ch in st.channels]
        st = self._states.get(source_id)
        return list(st.channels) if st else []

    @staticmethod
    def _matches(ch: Channel, filters: dict[str, Any]) -> bool:
        for key, val in filters.items():
            if val is None:
                continue
            got = getattr(ch, key, None)
            if key in ("country", "language") and got is not None:
                got = got.lower()
                val = str(val).lower()
            if got != val:
                return False
        return True

    def facet(self, facet: str, source_id: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        if facet not in FACETS:
            raise RpcError(INVALID_PARAMS, f"facet must be one of {FACETS}")
        counter: Counter[str] = Counter()
        for ch in self.channels_of(source_id):
            if not self._matches(ch, filters):
                continue
            counter[getattr(ch, facet) or ""] += 1
        return [{"value": k, "count": n} for k, n in sorted(counter.items(), key=lambda kv: (kv[0] == "", kv[0]))]

    def country_names(self) -> dict[str, dict[str, str]]:
        """ISO2 (lower) -> {name, flag} from the iptv-org API (cached a week); empty when unreachable."""
        if self._country_names is None:
            try:
                res = self.http.fetch(self.countries_url, ttl=7 * 24 * 3600, timeout=20)
                rows = json.loads(res.read_text())
                self._country_names = {r["code"].lower(): {"name": r.get("name", r["code"]), "flag": r.get("flag", "")}
                                       for r in rows if r.get("code")}
            except (FetchError, ValueError, KeyError, TypeError) as exc:
                log.warning("country names unavailable: %s", exc)
                self._country_names = {}
        return self._country_names

    def countries(self, source_id: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        names = self.country_names()
        out = []
        for row in self.facet("country", source_id, **filters):
            code = row["value"]
            if not code:
                continue
            meta = names.get(code, {})
            out.append({"code": code, "name": meta.get("name") or code.upper(), "flag": meta.get("flag", ""),
                        "count": row["count"]})
        return sorted(out, key=lambda r: r["name"].casefold())

    def list_channels(self, source_id: str | None = None, offset: int = 0, limit: int = 500, compact: bool = False,
                      **filters: Any) -> dict[str, Any]:
        items = [ch for ch in self.channels_of(source_id) if self._matches(ch, filters)]
        favs = self.store.favorite_ids()
        health = self.store.health()
        page = items[offset: offset + limit]
        return {
            "total": len(items), "offset": offset,
            "items": [self._decorate(ch, favs, health, compact) for ch in page],
        }

    def _decorate(self, ch: Channel, favs: set[str] | None = None, health: dict[str, Any] | None = None,
                  compact: bool = False) -> dict[str, Any]:
        if compact:  # what menus need; keeps big lists small on the wire
            d: dict[str, Any] = {"id": ch.id, "name": ch.name, "kind": ch.kind, "group": ch.group,
                                 "category": ch.category, "country": ch.country, "source": ch.source}
            if ch.extra.get("geo_blocked"):
                d["geo_blocked"] = True
            if ch.extra.get("not_24_7"):
                d["not_24_7"] = True
            if ch.extra.get("quality"):
                d["quality"] = ch.extra["quality"]
        else:
            d = ch.to_dict()
        d["favorite"] = ch.id in (favs if favs is not None else self.store.favorite_ids())
        h = (health if health is not None else self.store.health()).get(ch.id)
        d["health"] = h["ok"] if h else None
        return d

    def get(self, channel_id: str) -> Channel:
        ch = self._by_id.get(channel_id) or self.radio.stations.get(channel_id)
        if ch is None:
            for c in self.store.favorites() + self.store.recents(50):
                if c.id == channel_id:
                    return c
            raise RpcError(NOT_FOUND, f"unknown channel: {channel_id}")
        return ch

    def search(self, query: str, limit: int = 50, kind: str | None = None, source_id: str | None = None,
               compact: bool = False) -> list[dict[str, Any]]:
        favs = self.store.favorite_ids()
        health = self.store.health()
        return [self._decorate(ch, favs, health, compact)
                for ch in self._index.search(query, limit=limit, kind=kind, source=source_id)]

    def neighbours(self, channel_id: str, delta: int) -> Channel:
        """Zapping: the channel ``delta`` positions away inside the same source and group (wrapping)."""
        ch = self.get(channel_id)
        group = [c for c in self.channels_of(ch.source) if c.group == ch.group] or [ch]
        idx = next((i for i, c in enumerate(group) if c.id == ch.id), 0)
        return group[(idx + delta) % len(group)]

    def play_info(self, channel_id: str) -> dict[str, Any]:
        ch = self.get(channel_id)
        self.store.touch_recent(ch)
        if ch.source == RADIO_SOURCE and ch.extra.get("stationuuid"):
            uuid = ch.extra["stationuuid"]
            self.server.jobs.submit("radio.click", lambda job: asyncio.to_thread(self.radio.click, uuid),
                                    priority="index", meta={"uuid": uuid})
        return {"channel": self._decorate(ch), "url": ch.url, "options": ch.mpv_options()}

    async def radio_stations(self, country: str | None = None, tag: str | None = None, q: str | None = None,
                             top: int | None = None, limit: int = 200, compact: bool = False) -> list[dict[str, Any]]:
        if q:
            chans = await asyncio.to_thread(self.radio.search, q, limit, country)
        elif country:
            chans = await asyncio.to_thread(self.radio.by_country, country, limit)
        elif tag:
            chans = await asyncio.to_thread(self.radio.by_tag, tag, limit)
        else:
            chans = await asyncio.to_thread(self.radio.top, top or limit)
        favs = self.store.favorite_ids()
        health = self.store.health()
        return [self._decorate(c, favs, health, compact) for c in chans]

    # -- health -------------------------------------------------------------------------------

    async def probe(self, ch: Channel, timeout: float = 10.0) -> tuple[bool, str]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return False, "ffprobe not found"
        cmd = [ffprobe, "-v", "error", "-rw_timeout", str(int(timeout * 1_000_000)), "-icy", "0"]
        ua = ch.headers.get("User-Agent")
        if ua:
            cmd += ["-user_agent", ua]
        ref = ch.headers.get("Referer")
        if ref:
            cmd += ["-referer", ref]
        others = [f"{k}: {v}\r\n" for k, v in ch.headers.items() if k not in ("User-Agent", "Referer")]
        if others:
            cmd += ["-headers", "".join(others)]
        cmd += ["-show_entries", "stream=codec_type", "-of", "json", "-i", ch.url]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout + 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "timeout"
        if proc.returncode != 0:
            return False, err.decode("utf-8", "replace").strip()[-200:] or f"ffprobe exit {proc.returncode}"
        try:
            streams = json.loads(out or b"{}").get("streams", [])
        except ValueError:
            streams = []
        kinds = sorted({s.get("codec_type", "?") for s in streams})
        return bool(streams), ",".join(kinds)

    def submit_health_check(self, channels: list[Channel], concurrency: int = 4, session_id: str | None = None) -> Job:
        async def body(job: Job) -> dict[str, Any]:
            sem = asyncio.Semaphore(concurrency)
            done = 0
            ok = 0

            async def one(ch: Channel) -> None:
                nonlocal done, ok
                async with sem:
                    good, detail = await self.probe(ch)
                    self.store.set_health(ch.id, good, detail)
                    done += 1
                    ok += int(good)
                    job.report(done / max(1, len(channels)), f"{done}/{len(channels)} · {ok} ok")

            await asyncio.gather(*(one(ch) for ch in channels))
            return {"checked": done, "ok": ok}

        return self.server.jobs.submit("iptv.health", body, priority="index", heavy=False, session_id=session_id,
                                       meta={"channels": len(channels)})

    def close(self) -> None:
        self.store.close()


# -- JSON-RPC surface ------------------------------------------------------------------------------


def register(server: MpvdServer, service: IptvService) -> None:  # noqa: C901
    d = server.dispatcher
    server.services["iptv"] = True

    @d.method("iptv.sources")
    async def sources(ctx: RpcContext) -> list[dict[str, Any]]:
        """Configured sources with load state."""
        out = []
        for s in service.sources():
            st = service._states.get(s.id)
            out.append(st.to_dict() if st else SourceState(source=s).to_dict())
        return out

    @d.method("iptv.refresh")
    async def refresh(ctx: RpcContext, source: str | None = None, force: bool = False) -> list[dict[str, Any]]:
        """Download (respecting cache/TTL unless force) and parse sources."""
        states = await service.load_all(force=force, only=[source] if source else None)
        return [s.to_dict() for s in states]

    @d.method("iptv.facets")
    async def facets(ctx: RpcContext, facet: str, source: str | None = None, country: str | None = None,
                     category: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        """Distinct values with counts for group/country/category/language."""
        await service.ensure_loaded(source)
        return service.facet(facet, source, country=country, category=category, kind=kind)

    @d.method("iptv.countries")
    async def countries(ctx: RpcContext, source: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        """Countries with channel counts and display names/flags (sorted by name)."""
        await service.ensure_loaded(source)
        return await asyncio.to_thread(service.countries, source, kind=kind)

    @d.method("iptv.channels")
    async def channels(ctx: RpcContext, source: str | None = None, group: str | None = None,
                       country: str | None = None, category: str | None = None, language: str | None = None,
                       kind: str | None = None, offset: int = 0, limit: int = 500, compact: bool = False) -> dict[str, Any]:
        """Channels filtered by facets, paginated (compact=true for menus)."""
        await service.ensure_loaded(source)
        return service.list_channels(source, offset, limit, compact, group=group, country=country, category=category,
                                     language=language, kind=kind)

    @d.method("iptv.search")
    async def search(ctx: RpcContext, q: str, limit: int = 50, kind: str | None = None,
                     source: str | None = None, compact: bool = False) -> list[dict[str, Any]]:
        """Accent-insensitive search across loaded sources."""
        await service.ensure_loaded()
        return service.search(q, limit=limit, kind=kind, source_id=source, compact=compact)

    @d.method("iptv.channel")
    async def channel(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """One channel by id."""
        await service.ensure_loaded()
        return service._decorate(service.get(id))

    @d.method("iptv.play")
    async def play(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """URL + per-file mpv options for a channel; records it as recent."""
        await service.ensure_loaded()
        return service.play_info(id)

    @d.method("iptv.zap")
    async def zap(ctx: RpcContext, id: str, delta: int = 1) -> dict[str, Any]:  # noqa: A002
        """Neighbour channel inside the same source/group (wraps around)."""
        await service.ensure_loaded()
        return service.play_info(service.neighbours(id, int(delta)).id)

    @d.method("iptv.favorites.list")
    async def fav_list(ctx: RpcContext, compact: bool = False) -> list[dict[str, Any]]:
        """Favourite channels in user order."""
        return [service._decorate(c, compact=compact) for c in service.store.favorites()]

    @d.method("iptv.favorites.toggle")
    async def fav_toggle(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Add/remove a favourite; returns the new state."""
        await service.ensure_loaded()
        return {"id": id, "favorite": service.store.toggle_favorite(service.get(id))}

    @d.method("iptv.recents.list")
    async def rec_list(ctx: RpcContext, limit: int = 20, compact: bool = False) -> list[dict[str, Any]]:
        """Recently played channels."""
        return [service._decorate(c, compact=compact) for c in service.store.recents(limit)]

    @d.method("iptv.recents.clear")
    async def rec_clear(ctx: RpcContext) -> dict[str, Any]:
        """Forget recents."""
        service.store.clear_recents()
        return {"ok": True}

    @d.method("iptv.sources.add")
    async def src_add(ctx: RpcContext, url: str, name: str, kind: str = "tv", id: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Add a user M3U source (id defaults to user:<name>)."""
        if kind not in ("tv", "radio"):
            raise RpcError(INVALID_PARAMS, "kind must be tv or radio")
        sid = id or "user:" + "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
        service.store.add_source(sid, name, url, kind)
        return (await service.load(sid, force=True)).to_dict()

    @d.method("iptv.sources.remove")
    async def src_remove(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Remove a user source."""
        removed = service.store.remove_source(id)
        service._states.pop(id, None)
        service._rebuild_index()
        return {"removed": removed}

    @d.method("radio.countries")
    async def radio_countries(ctx: RpcContext) -> list[dict[str, Any]]:
        """Countries in Radio Browser with station counts."""
        try:
            return await asyncio.to_thread(service.radio.countries)
        except Exception as exc:  # noqa: BLE001 - network
            raise RpcError(UNAVAILABLE, f"radio-browser: {exc}") from exc

    @d.method("radio.tags")
    async def radio_tags(ctx: RpcContext, limit: int = 100) -> list[dict[str, Any]]:
        """Most used tags (genres) in Radio Browser."""
        try:
            return await asyncio.to_thread(service.radio.tags, limit)
        except Exception as exc:  # noqa: BLE001
            raise RpcError(UNAVAILABLE, f"radio-browser: {exc}") from exc

    @d.method("radio.stations")
    async def radio_stations(ctx: RpcContext, country: str | None = None, tag: str | None = None, q: str | None = None,
                             top: int | None = None, limit: int = 200, compact: bool = False) -> list[dict[str, Any]]:
        """Stations by country code, tag, name search or top votes (as channels, playable with iptv.play)."""
        try:
            return await service.radio_stations(country=country, tag=tag, q=q, top=top, limit=limit, compact=compact)
        except Exception as exc:  # noqa: BLE001
            raise RpcError(UNAVAILABLE, f"radio-browser: {exc}") from exc

    @d.method("iptv.health.check")
    async def health_check(ctx: RpcContext, source: str | None = None, ids: list[str] | None = None,
                           limit: int = 200) -> dict[str, Any]:
        """Probe channels with ffprobe in the background (low priority); returns the job."""
        await service.ensure_loaded(source)
        if ids:
            chans = [service.get(i) for i in ids]
        else:
            chans = service.channels_of(source)[:limit]
        if not chans:
            raise RpcError(UNAVAILABLE, "no channels to check")
        sid = ctx.session.id if ctx.session else None
        return service.submit_health_check(chans, session_id=sid).to_dict()

    @d.method("iptv.health.status")
    async def health_status(ctx: RpcContext) -> dict[str, Any]:
        """Health results by channel id."""
        return service.store.health()
