"""IPTV service: sources, catalogue, facets, search, playback options, favourites/recents, health checks.

Exposed as ``iptv.*`` JSON-RPC methods (see ``register``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import urllib.request
from collections import Counter
from typing import TYPE_CHECKING, Any

from mpvd.iptv import labels
from mpvd.iptv import tracks as trk
from mpvd.iptv.index import SearchIndex, normalize
from mpvd.iptv.model import Channel, best_variant, hls_variants
from mpvd.iptv.radiobrowser import SOURCE_ID as RADIO_SOURCE, RadioBrowser
from mpvd.iptv.sources import BUILTIN_SOURCES, Source, SourceState, load_source
from mpvd.iptv.store import IptvStore
from mpvd.jobs import Job
from mpvd.net import HttpCache
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.iptv")

FACETS = ("group", "country", "category", "language")
SEARCH_SCOPES = ("favorites", "recents", "radio", "radio_top")  # besides the catalogue (lists loaded by mpvd)
MASTER_MAX_BYTES = 1 << 20  # an HLS master playlist is a few KB; never read a stream by mistake


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
        self._dups: dict[str, list[str]] = {}  # channel id -> ids of the same channel in its list, preferred first
        self._loads: dict[str, asyncio.Task[SourceState]] = {}
        self._lock = asyncio.Lock()
        self._probed: dict[str, list[dict[str, Any]]] = {}  # channel id -> streams of its last ffprobe (tracks)
        import os  # noqa: PLC0415

        self.radio = RadioBrowser(self.http, base_url=radio_base_url or os.environ.get("MPV_UOS_RADIO_BROWSER_URL"))

    # -- sources ------------------------------------------------------------------------------

    def sources(self) -> list[Source]:
        user = [Source(s["id"], s["name"], s["url"], s["kind"], "user", builtin=False)
                for s in self.store.sources() if s["enabled"]]
        return self._builtin + user

    def source(self, source_id: str) -> Source:
        for s in self.sources():
            if s.id == source_id:
                return s
        raise RpcError(NOT_FOUND, f"esa lista ya no está: {source_id}")

    async def load(self, source_id: str, force: bool = False, rebuild: bool = True) -> SourceState:
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
            if rebuild:
                await self._rebuild_index_async()
        return self._states[source_id]

    async def load_all(self, force: bool = False, only: list[str] | None = None) -> list[SourceState]:
        ids = only or [s.id for s in self.sources()]
        # the index is rebuilt once, at the end: it is O(all channels) (iptv-org alone is ~12 000 entries, each one
        # normalised half a dozen times), and doing it per source froze the event loop three times over
        out = list(await asyncio.gather(*(self.load(sid, force, rebuild=False) for sid in ids)))
        async with self._lock:
            await self._rebuild_index_async()
        return out

    async def _rebuild_index_async(self) -> None:
        """Rebuild in a thread: with every list loaded this is hundreds of thousands of string normalisations, and in
        the loop it made mpvd stop answering mpv and the menus while «Actualizar listas» ran."""
        await asyncio.to_thread(self._rebuild_index)

    def _rebuild_index(self) -> None:
        self._index = SearchIndex()
        self._by_id = {}
        self._dups = {}
        for state in self._states.values():
            groups: dict[tuple[str, str, str, str], list[Channel]] = {}
            for ch in state.channels:
                self._by_id[ch.id] = ch
                self._index.add(ch)
                groups.setdefault(labels.duplicate_key(ch), []).append(ch)
            for members in groups.values():
                if len(members) < 2:
                    continue
                # the broadcaster's own stream first, FAST copies (ads inserted) last; list order otherwise
                ids = [c.id for c in sorted(members, key=lambda c: labels.has_ads(c.url))]
                for cid in ids:
                    self._dups[cid] = ids

    def alternatives_of(self, channel_id: str) -> list[Channel]:
        """Other copies of the same channel (same list, name and group), in the order to try them."""
        return [self._by_id[i] for i in self._dups.get(channel_id, []) if i != channel_id and i in self._by_id]

    def primary_id(self, channel_id: str) -> str:
        ids = self._dups.get(channel_id)
        return ids[0] if ids else channel_id

    def _merge(self, channels: list[Channel]) -> list[tuple[Channel, int]]:
        """One entry per channel: the preferred copy, with how many alternatives it has."""
        present = {c.id for c in channels}
        out = []
        for ch in channels:
            ids = self._dups.get(ch.id)
            if ids:
                first = next(i for i in ids if i in present)
                if first != ch.id:
                    continue
                out.append((ch, sum(1 for i in ids if i != ch.id)))
            else:
                out.append((ch, 0))
        return out

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

    @staticmethod
    def _home_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The user's country (labels.user_country) on top, marked with ``home``; the rest keep their order."""
        home = labels.user_country()
        top = [r for r in rows if labels.country_code(r["code"]) == home]
        for r in top:
            r["home"] = True
        return top + [r for r in rows if labels.country_code(r["code"]) != home]

    def countries(self, source_id: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        """Countries with channels, named in Spanish (system iso-codes), home country first, then A-Z."""
        out = []
        for row in self.facet("country", source_id, **filters):
            code = row["value"]
            if not code:
                continue
            out.append({"code": code, "name": labels.country_name(code), "flag": labels.flag(code), "count": row["count"]})
        out.sort(key=lambda r: normalize(r["name"]))
        return self._home_first(out)

    def radio_countries(self) -> list[dict[str, Any]]:
        """Radio Browser countries in Spanish, home country first, then by number of stations."""
        rows = [{**r, "name": labels.country_name(r["code"], r.get("name")), "flag": labels.flag(r["code"])}
                for r in self.radio.countries()]
        return self._home_first(rows)

    def list_channels(self, source_id: str | None = None, offset: int = 0, limit: int = 500, compact: bool = False,
                      merge: bool = False, **filters: Any) -> dict[str, Any]:
        """``merge``: one entry per channel when a list repeats it (see ``alternatives_of``)."""
        items = [ch for ch in self.channels_of(source_id) if self._matches(ch, filters)]
        rows = self._merge(items) if merge else [(ch, 0) for ch in items]
        favs = self.store.favorite_ids()
        health = self.store.health()
        tracks = self.store.tracks()
        page = rows[offset: offset + limit]
        return {
            "total": len(rows), "offset": offset,
            "items": [self._decorate(ch, favs, health, compact, alternatives=n, tracks=tracks) for ch, n in page],
        }

    def _merged_health(self, ch: Channel, health: dict[str, Any]) -> bool | None:
        """A repeated channel is alive when any of its copies is (mu-iptv falls back to it by itself)."""
        own = health.get(ch.id)
        if own and own["ok"]:
            return True
        if any((health.get(a.id) or {}).get("ok") for a in self.alternatives_of(ch.id)):
            return True
        return own["ok"] if own else None

    def _health_counts(self, ch: Channel, health: dict[str, Any]) -> tuple[int, int]:
        """(copias comprobadas, copias que respondieron) del canal y sus espejos. H39/E2: la lista tiene que decir lo
        que ya se sabe en vez de dejar probar a ciegas."""
        comprobadas = respondieron = 0
        for c in (ch, *self.alternatives_of(ch.id)):
            h = health.get(c.id)
            if h is not None:
                comprobadas += 1
                respondieron += 1 if h["ok"] else 0
        return comprobadas, respondieron

    def _badges(self, ch: Channel, tracks: dict[str, Any]) -> list[str]:
        """CC / VO / AD of a channel (mpvd/iptv/tracks): its own tracks, else those of another copy of it."""
        for c in (ch, *self.alternatives_of(ch.id)):
            known = tracks.get(c.id)
            if known is not None:
                return list(known.get("badges") or [])
        return []

    def _decorate(self, ch: Channel, favs: set[str] | None = None, health: dict[str, Any] | None = None,
                  compact: bool = False, alternatives: int = 0, tracks: dict[str, Any] | None = None) -> dict[str, Any]:
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
        # Spanish display labels (the raw values stay for filters and the other APIs)
        if ch.source == RADIO_SOURCE and ch.country:
            d["group_label"] = labels.country_name(ch.country, ch.group)
        else:
            d["group_label"] = labels.group_label(ch.group)
        d["category_label"] = labels.category_label(ch.category)
        if labels.has_ads(ch.url):
            d["ads"] = True
        if alternatives:
            d["alternatives"] = alternatives
        d["favorite"] = ch.id in (favs if favs is not None else self.store.favorite_ids())
        health = health if health is not None else self.store.health()
        h = health.get(ch.id)
        d["health"] = self._merged_health(ch, health) if alternatives else (h["ok"] if h else None)
        # H39/E2: con el resultado de la comprobación va POR QUÉ y de cuándo es, y en un canal repetido cuántas de sus
        # copias respondieron: «+5 fuentes» sin más no dice si merece la pena probar.
        if h is not None:
            d["health_at"] = h.get("checked_at")
            if not h["ok"]:
                d["health_detail"] = labels.health_reason(h.get("detail") or "")
        if alternatives:
            comprobadas, respondieron = self._health_counts(ch, health)
            if comprobadas:
                d["health_checked"] = comprobadas
                d["health_alive"] = respondieron
        quality = h.get("quality") if h else None
        if quality:
            d["quality_label"] = labels.quality_label(quality)
            if labels.low_bitrate(quality):
                d["low_bitrate"] = True
        elif ch.extra.get("quality"):
            d["quality_label"] = ch.extra["quality"]  # what the list title says ("(720p)")
        badges = self._badges(ch, tracks if tracks is not None else self.store.tracks())
        if badges:
            d["badges"] = badges
        return d

    def get(self, channel_id: str) -> Channel:
        ch = self._by_id.get(channel_id) or self.radio.stations.get(channel_id)
        if ch is None:
            for c in self.store.favorites() + self.store.recents(50):
                if c.id == channel_id:
                    return c
            raise RpcError(NOT_FOUND, "ese canal ya no está en la lista (prueba a actualizarla)")
        return ch

    def search(self, query: str, limit: int = 50, kind: str | None = None, source_id: str | None = None,
               compact: bool = False, merge: bool = False, **filters: Any) -> list[dict[str, Any]]:
        """Accent-insensitive search in the loaded lists; ``filters`` (group, country, category, language) keep it
        inside one list of the menus ("Buscar en esta lista")."""
        filters = {k: v for k, v in filters.items() if v is not None}
        where = (lambda ch: self._matches(ch, filters)) if filters else None
        found = self._index.search(query, limit=limit, kind=kind, source=source_id, where=where)
        return self.decorate_found(found, compact, merge)

    def decorate_found(self, found: list[Channel], compact: bool = False, merge: bool = False) -> list[dict[str, Any]]:
        favs = self.store.favorite_ids()
        health = self.store.health()
        tracks = self.store.tracks()
        rows = self._merge(found) if merge else [(ch, 0) for ch in found]
        return [self._decorate(ch, favs, health, compact, alternatives=n, tracks=tracks) for ch, n in rows]

    async def search_scope(self, query: str, scope: str, limit: int = 50, country: str | None = None,
                           pool: int | None = None, compact: bool = False) -> list[dict[str, Any]]:
        """"Buscar en esta lista" for the lists that are not a slice of the catalogue, with the same engine
        (SearchIndex: every word, accents ignored): favourites, recents, a country of Radio Browser (the stations
        its list shows, ``pool``), its most voted, or the whole Radio Browser (asked by name, as typed and without
        accents, then ranked here)."""
        if scope == "favorites":
            chans = self.store.favorites()
        elif scope == "recents":
            chans = self.store.recents(self.store.max_recents)
        elif scope == "radio_top":
            chans = await asyncio.to_thread(self.radio.top, int(pool or 100))
        elif scope == "radio" and country:
            chans = await asyncio.to_thread(self.radio.by_country, country, int(pool or 300))
        elif scope == "radio":
            chans = []
            for q in dict.fromkeys([query.strip(), normalize(query)]):  # "Ràdio" and "radio": the directory cares
                if q:
                    chans += await asyncio.to_thread(self.radio.search, q, max(limit, 50))
        else:
            raise RpcError(INVALID_PARAMS, f"scope must be one of {SEARCH_SCOPES}")
        unique = list({ch.id: ch for ch in chans}.values())
        return self.decorate_found(SearchIndex(unique).search(query, limit=limit), compact)

    def neighbours(self, channel_id: str, delta: int) -> Channel:
        """Zapping: the channel ``delta`` positions away inside the same source and group (wrapping); the
        other copies of a repeated channel are skipped (they are its alternatives, not other channels)."""
        ch = self.get(self.primary_id(channel_id))
        group = [c for c in self.channels_of(ch.source) if c.group == ch.group and self.primary_id(c.id) == c.id] or [ch]
        idx = next((i for i, c in enumerate(group) if c.id == ch.id), 0)
        return group[(idx + delta) % len(group)]

    def play_info(self, channel_id: str) -> dict[str, Any]:
        ch = self.get(channel_id)
        self.store.touch_recent(ch)
        if ch.source == RADIO_SOURCE and ch.extra.get("stationuuid"):
            uuid = ch.extra["stationuuid"]
            self.server.jobs.submit("radio.click", lambda job: asyncio.to_thread(self.radio.click, uuid),
                                    priority="index", meta={"uuid": uuid})
        # mu-iptv tries these in order when the stream fails to open (end-file error)
        alternatives = [{"id": a.id, "name": a.name, "url": a.url, "options": a.mpv_options(),
                         "ads": labels.has_ads(a.url)} for a in self.alternatives_of(ch.id)]
        return {"channel": self._decorate(ch), "url": ch.url, "options": ch.mpv_options(), "alternatives": alternatives}

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

    async def probe(self, ch: Channel, timeout: float = 10.0) -> tuple[bool, str, dict[str, Any] | None]:
        """ffprobe the stream with the same headers mpv sends -> (alive, detail, best video {height, fps}).

        The audio/subtitle streams it saw (language, dispositions) are left in ``self._probed[ch.id]``."""
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return False, "ffprobe not found", None
        cmd = [ffprobe, "-v", "error", "-rw_timeout", str(int(timeout * 1_000_000)), "-icy", "0"]
        headers = ch.request_headers()
        ua = headers.get("User-Agent")
        if ua:
            cmd += ["-user_agent", ua]
        ref = headers.get("Referer")
        if ref:
            cmd += ["-referer", ref]
        others = [f"{k}: {v}\r\n" for k, v in headers.items() if k not in ("User-Agent", "Referer")]
        if others:
            cmd += ["-headers", "".join(others)]
        cmd += ["-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate"
                ":stream_tags=language,title:stream_disposition=visual_impaired,hearing_impaired,forced",
                "-of", "json", "-i", ch.url]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout + 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "timeout", None
        if proc.returncode != 0:
            return False, err.decode("utf-8", "replace").strip()[-200:] or f"ffprobe exit {proc.returncode}", None
        try:
            streams = json.loads(out or b"{}").get("streams", [])
        except ValueError:
            streams = []
        kinds = sorted({s.get("codec_type", "?") for s in streams})
        self._probed[ch.id] = _as_track_list(streams)
        return bool(streams), ",".join(kinds), _best_video(streams)

    def master_text(self, ch: Channel, timeout: float = 10.0) -> str | None:
        """The channel's HLS playlist as the player gets it (its headers), at most MASTER_MAX_BYTES."""
        req = urllib.request.Request(ch.url, headers={**ch.request_headers(), "Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - channel URL, http(s) only
                return resp.read(MASTER_MAX_BYTES).decode("utf-8", "replace")
        except (OSError, ValueError) as exc:
            log.info("hls master %s: %s", ch.url, exc)
            return None

    @staticmethod
    def master_quality(text: str | None) -> dict[str, Any] | None:
        best = best_variant(hls_variants(text or ""))
        if not best:
            return None
        return {k: best[k] for k in ("height", "width", "fps", "bandwidth") if best.get(k)}

    def hls_master(self, ch: Channel, timeout: float = 10.0) -> dict[str, Any] | None:
        """Best variant of an HLS master playlist ({height, width, fps, bandwidth}); None for media playlists."""
        return self.master_quality(self.master_text(ch, timeout))

    def master_renditions(self, ch: Channel, timeout: float = 8.0) -> list[dict[str, Any]] | None:
        """Audio/subtitle renditions (#EXT-X-MEDIA) of an HLS channel; None when the playlist cannot be read."""
        text = self.master_text(ch, timeout) if ch.is_hls() else None
        return trk.parse_master_media(text) if text is not None else None

    async def check_one(self, ch: Channel) -> tuple[bool, str, dict[str, Any] | None]:
        """Health + quality: the master playlist says resolution/fps/bitrate; ffprobe fills what it omits.
        Also learns the channel's audio/subtitle tracks (badges CC / VO / AD): the master's renditions (with
        their names), else the streams ffprobe saw."""
        good, detail, video = await self.probe(ch)
        probed = self._probed.pop(ch.id, [])
        quality: dict[str, Any] = {}
        renditions: list[dict[str, Any]] | None = None
        if good and ch.is_hls():
            text = await asyncio.to_thread(self.master_text, ch)
            quality = self.master_quality(text) or {}
            renditions = trk.parse_master_media(text) if text is not None else None
        if good:
            self._learn_tracks(ch, renditions, probed)
        if good and video:
            if not quality.get("height"):
                quality.update({k: v for k, v in video.items() if v})
            elif not quality.get("fps") and video.get("fps") and video.get("height") == quality["height"]:
                quality["fps"] = video["fps"]
        return good, detail, quality or None

    def _learn_tracks(self, ch: Channel, renditions: list[dict[str, Any]] | None,
                      probed: list[dict[str, Any]]) -> None:
        if renditions:
            summary = trk.summary([dict(r) for r in renditions], "master")
            self.store.set_tracks(ch.id, summary, "master", renditions)
        elif probed:
            summary = trk.summary(trk.label_player_tracks(probed), "probe")
            self.store.set_tracks(ch.id, summary, "probe", renditions)

    async def player_tracks(self, track_list: list[dict[str, Any]], channel_id: str | None = None) -> dict[str, Any]:
        """Names for the tracks mpv shows while a channel plays (menu «Audio y subtítulos del canal»); with the
        channel id they are also stored for its badges. The HLS master is read once per channel for the NAMEs
        FFmpeg drops."""
        ch = None
        if channel_id:
            try:
                ch = self.get(channel_id)
            except RpcError:
                ch = None
        renditions = None
        if ch is not None:
            info = self.store.track_info(ch.id)
            renditions = info["renditions"] if info else None
            if renditions is None and ch.is_hls():
                renditions = await asyncio.to_thread(self.master_renditions, ch)
        labelled = trk.label_player_tracks(track_list, renditions)
        summary = trk.summary([dict(t) for t in labelled], "player")
        if ch is not None and labelled:
            self.store.set_tracks(ch.id, summary, "player", renditions)
        keep = ("type", "id", "label", "role", "lang", "selected", "forced", "cc", "external")
        return {"channel": ch.id if ch else None, "tracks": [{k: t.get(k) for k in keep} for t in labelled],
                "badges": summary["badges"]}

    def submit_health_check(self, channels: list[Channel], concurrency: int = 4, session_id: str | None = None) -> Job:
        async def body(job: Job) -> dict[str, Any]:
            sem = asyncio.Semaphore(concurrency)
            done = 0
            ok = 0

            async def one(ch: Channel) -> None:
                nonlocal done, ok
                async with sem:
                    good, detail, quality = await self.check_one(ch)
                    self.store.set_health(ch.id, good, detail, quality)
                    done += 1
                    ok += int(good)
                    job.report(done / max(1, len(channels)), f"{done}/{len(channels)} · {ok} ok")

            await asyncio.gather(*(one(ch) for ch in channels))
            return {"checked": done, "ok": ok}

        return self.server.jobs.submit("iptv.health", body, priority="index", heavy=False, session_id=session_id,
                                       meta={"channels": len(channels)})

    def close(self) -> None:
        self.store.close()


def _fps(rate: str | None) -> float | None:
    """ffprobe "25/1" / "30000/1001" -> 25.0 / 29.97 (None for "0/0")."""
    try:
        num, _, den = (rate or "").partition("/")
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if 0 < value < 1000 else None


def _as_track_list(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ffprobe streams -> entries shaped like mpv's track-list (what mpvd/iptv/tracks reads)."""
    out = []
    for i, s in enumerate(streams):
        kind = {"audio": "audio", "subtitle": "sub"}.get(s.get("codec_type", ""))
        if not kind:
            continue
        tags = s.get("tags") or {}
        disp = s.get("disposition") or {}
        out.append({"type": kind, "id": i + 1, "lang": tags.get("language"), "title": tags.get("title"),
                    "codec": s.get("codec_name"), "forced": bool(disp.get("forced")),
                    "visual-impaired": bool(disp.get("visual_impaired")),
                    "hearing-impaired": bool(disp.get("hearing_impaired"))})
    return out


def _best_video(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    videos = [s for s in streams if s.get("codec_type") == "video" and s.get("height")]
    if not videos:
        return None
    best = max(videos, key=lambda s: (s.get("height") or 0, _fps(s.get("avg_frame_rate")) or 0))
    return {"height": best.get("height"), "width": best.get("width"),
            "fps": _fps(best.get("avg_frame_rate")) or _fps(best.get("r_frame_rate"))}


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
                       kind: str | None = None, offset: int = 0, limit: int = 500, compact: bool = False,
                       merge: bool = False) -> dict[str, Any]:
        """Channels filtered by facets, paginated (compact=true for menus; merge=true: one entry per repeated
        channel, the others become its alternatives)."""
        await service.ensure_loaded(source)
        return service.list_channels(source, offset, limit, compact, merge, group=group, country=country,
                                     category=category, language=language, kind=kind)

    @d.method("iptv.search")
    async def search(ctx: RpcContext, q: str, limit: int = 50, kind: str | None = None,
                     source: str | None = None, compact: bool = False, merge: bool = False,
                     group: str | None = None, country: str | None = None, category: str | None = None,
                     scope: str | None = None, pool: int | None = None) -> list[dict[str, Any]]:
        """Accent-insensitive search across loaded sources. «Buscar en esta lista»: ``source``/``group``/
        ``country``/``category`` keep it inside one list; ``scope`` searches favourites, recents or Radio Browser
        ("radio" with or without ``country``, "radio_top"; ``pool`` = how many stations that list shows)."""
        if scope:
            try:
                return await service.search_scope(q, scope, limit=limit, country=country, pool=pool, compact=compact)
            except RpcError:
                raise
            except Exception as exc:  # noqa: BLE001 - network (Radio Browser)
                raise RpcError(UNAVAILABLE, f"radio-browser: {exc}") from exc
        if source:
            await service.ensure_loaded(source)
        else:
            await service.ensure_loaded()
        return service.search(q, limit=limit, kind=kind, source_id=source, compact=compact, merge=merge,
                              group=group, country=country, category=category)

    @d.method("iptv.tracks")
    async def tracks(ctx: RpcContext, tracks: list[dict[str, Any]], id: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Readable names for mpv's ``track-list`` of a channel (audio: Español / Versión original /
        Audiodescripción...; subtitles: language, «para sordos», forced) and its badges; with ``id`` they are stored
        for the CC / VO / AD hints of the lists."""
        if not isinstance(tracks, list):
            raise RpcError(INVALID_PARAMS, "tracks must be mpv's track-list")
        return await service.player_tracks([t for t in tracks if isinstance(t, dict)], id)

    @d.method("iptv.tracks.get")
    async def tracks_get(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """What mpvd knows about a channel's tracks (summary, source, renditions of its master) or {}."""
        return service.store.track_info(id) or {}

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
        return service.decorate_found(service.store.favorites(), compact)

    @d.method("iptv.favorites.toggle")
    async def fav_toggle(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Add/remove a favourite; returns the new state."""
        await service.ensure_loaded()
        return {"id": id, "favorite": service.store.toggle_favorite(service.get(id))}

    @d.method("iptv.recents.list")
    async def rec_list(ctx: RpcContext, limit: int = 20, compact: bool = False) -> list[dict[str, Any]]:
        """Recently played channels."""
        return service.decorate_found(service.store.recents(limit), compact)

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
        """Countries in Radio Browser with station counts (Spanish names, the user's country first)."""
        try:
            return await asyncio.to_thread(service.radio_countries)
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
