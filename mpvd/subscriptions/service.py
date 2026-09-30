"""``feeds.*`` (H23): subscriptions to channels, lists and podcasts checked in the background, downloaded by the
download manager at low priority when the rules allow it, trimmed («conservar N», «borrar lo visto») and passed through
the post-download chain.

* **Check** (a light job of INDEX priority, every ``interval_h`` hours or «Comprobar ahora»): channels and lists with
  ``yt-dlp --flat-playlist -J -I 1:N`` (a channel only looks at its newest ``scan_depth`` videos), podcasts with the HTTP
  cache (ETag). New = an id this subscription never saw; the first check only takes the ``initial`` newest.
* **Download**: what is pending goes to the DownloadManager one (``parallel``) at a time, only when the rules allow it
  (time window, limit per window, not on a metered connection); lists and channels use yt-dlp's archive so nothing is
  downloaded twice, podcasts are named «fecha - título» (their files often have a bare number as name).
* **Rules** after each download and check: keep the N newest (only watched ones, or by age), delete what was watched
  (after ``watched_grace_h``). Only files this subscription downloaded, and only inside its own folders, are deleted.
* Events to mpv: ``mu-event {"event":"feeds", …}`` to ``mu_feeds``.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.jobs import Priority, Status
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.subscriptions import detect as detect_mod
from mpvd.subscriptions.chain import RENAME_PRESETS, ChainConfig, PostChain, safe_component
from mpvd.subscriptions.rss import FeedError, parse_feed
from mpvd.subscriptions.rules import (
    detect_metered,
    format_window,
    gate,
    next_open,
    parse_window,
    period_start,
)
from mpvd.subscriptions.store import DEFAULT_INITIAL, PRESET_IDS, FeedStore, Subscription
from mpvd.ytdl.downloads import FINAL, default_media_dir
from mpvd.ytdl.presets import PRESETS

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.ytdl.downloads import DownloadItem

log = logging.getLogger("mpvd.subscriptions")

NOTIFY = "mu_feeds"
MAX_ATTEMPTS = 3
PLAYLIST_MAX = 500
METERED_TTL = 60.0
SWEEP_EVERY = 1800.0
PRESET_TITLES = {p["id"]: p["title"] for p in PRESETS}


def _now_local() -> dt.datetime:
    return dt.datetime.now()


def inside(path: Path, roots: list[Path]) -> bool:
    """``path`` (symlinks resolved) is below one of ``roots`` (also resolved), never a root itself."""
    try:
        real = path.resolve()
    except OSError:
        return False
    for r in roots:
        try:
            rr = r.resolve()
        except OSError:
            continue
        if rr in real.parents:
            return True
    return False


class FeedsService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.ytdl = server.ytdl
        self.downloads = server.ytdl.downloads
        self.store = FeedStore(server.settings.data_dir / "feeds.json")
        self.http = server.ytdl.http
        self.clock = _now_local                 # local time (tests inject theirs)
        self.metered_fn = detect_metered
        self.metered: bool | None = None
        self._metered_at = 0.0
        self.tick_seconds = float(os.environ.get("MPV_UOS_FEEDS_TICK", "60"))
        self.inflight: dict[str, str] = {}     # download id → subscription id
        self.checks: dict[str, Any] = {}       # subscription id → check job
        self.reason = ""                        # why nothing downloads right now («fuera de la franja…»)
        self._closing = False
        self._pausing: set[str] = set()         # downloads cancelled by the metered pause (queued again)
        self._loop_task: asyncio.Task[None] | None = None
        self._last_sweep = 0.0
        self._dispatch_handle: asyncio.Handle | None = None
        self.chain = PostChain(server, self.downloads, on_complete=self._chain_done)
        self.downloads.final_hooks.append(self._final)

    # -- lifecycle ------------------------------------------------------------------------------------------------

    @property
    def settings(self):  # noqa: ANN201 - FeedSettings
        return self.store.settings

    async def start(self) -> None:
        for item in list(self.downloads.items.values()):
            sid = item.spec.extra.get("feed")
            if sid and item.status not in FINAL and sid in self.store.subs:
                self.inflight[item.id] = sid         # resumed by the download manager (yt-dlp --continue)
            elif item.status == "done" and (item.post or {}).get("status") in ("pending", "running"):
                cfg = self._chain_for(item)
                if cfg is not None:                  # mpvd stopped in the middle of the chain: go on
                    self.chain.start(item, cfg)
        self._loop_task = asyncio.create_task(self._loop(), name="mpvd-feeds")

    async def close(self) -> None:
        self._closing = True     # downloads cancelled while stopping go back to «pending» (resumed next time)
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(BaseException):
                await self._loop_task
        await self.chain.close()
        self.store.save()

    def busy(self) -> bool:
        """Keeps mpvd alive after the player closed: subscription work in progress, or «seguir comprobando»."""
        if self.inflight or self.chain.running() or any(j.status in (Status.QUEUED, Status.RUNNING)
                                                        for j in self.checks.values()):
            return True
        return self.settings.keep_running and any(not s.paused for s in self.store.subs.values())

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("feeds tick failed")
            await asyncio.sleep(self.tick_seconds)

    async def refresh_metered(self, force: bool = False) -> bool | None:
        if force or time.monotonic() - self._metered_at > METERED_TTL or self._metered_at == 0.0:
            self.metered = await asyncio.to_thread(self.metered_fn)
            self._metered_at = time.monotonic()
        return self.metered

    async def tick(self) -> None:
        now = time.time()
        metered = await self.refresh_metered()
        if not (self.settings.pause_metered and metered):
            for sub in list(self.store.subs.values()):
                if not sub.paused and now - sub.last_check >= self.settings.interval_h * 3600:
                    self.check_later(sub)
        self.dispatch()
        if now - self._last_sweep > SWEEP_EVERY:
            self._last_sweep = now
            for sub in list(self.store.subs.values()):
                await self.apply_rules(sub)

    # -- events ---------------------------------------------------------------------------------------------------

    def _push(self, sub: Subscription | None = None, event: str = "feeds") -> None:
        payload: dict[str, Any] = {"event": event, "state": self.state()}
        key = "feeds:state"
        status = f"{self.reason}:{len(self.inflight)}"
        if sub is not None:
            payload["subscription"] = self.public(sub)
            key = "feeds:" + sub.id
            status = f"{self.status_of(sub)}:{len(sub.pending)}:{len(sub.files)}:{sub.last_check}:{sub.last_error}"
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(NOTIFY, key, status, payload, min_interval=0.25)

    # -- views ----------------------------------------------------------------------------------------------------

    def status_of(self, sub: Subscription) -> str:
        job = self.checks.get(sub.id)
        if job is not None and job.status in (Status.QUEUED, Status.RUNNING):
            return "checking"
        if any(v == sub.id for v in self.inflight.values()):
            return "downloading"
        if sub.paused:
            return "paused"
        return "pending" if sub.pending else "idle"

    def chain_of(self, sub: Subscription) -> ChainConfig:
        return ChainConfig.from_dict(sub.chain if sub.chain is not None else self.settings.chain)

    def roots(self, sub: Subscription) -> list[Path]:
        """Where this subscription's files may live (and may be deleted): its folder and the library folder.

        The library subfolder is named after the title the subscription had when the file was moved, so after a rename
        the old subfolder no longer matched and neither «conservar N» nor «borrar lo visto» deleted anything. Only paths
        this subscription recorded itself are ever deleted, and the library folder is one the owner chose.
        """
        out = [Path(os.path.expanduser(sub.folder))]
        cfg = self.chain_of(sub)
        if cfg.move_to:
            out.append(Path(os.path.expanduser(cfg.move_to)))
        return out

    def public(self, sub: Subscription) -> dict[str, Any]:
        chain = self.chain_of(sub)
        return {
            "id": sub.id, "url": sub.url, "kind": sub.kind, "kind_label": detect_mod.KIND_LABELS.get(sub.kind, sub.kind),
            "title": sub.title, "preset": sub.preset, "preset_title": PRESET_TITLES.get(sub.preset, sub.preset),
            "container": sub.container, "sponsorblock": sub.sponsorblock, "keep": sub.keep,
            "keep_watched_only": sub.keep_watched_only, "delete_watched": sub.delete_watched, "initial": sub.initial,
            "chain": chain.to_dict(), "chain_custom": sub.chain is not None, "folder": sub.folder,
            "paused": sub.paused, "status": self.status_of(sub), "created_at": sub.created_at,
            "last_check": sub.last_check, "next_check": (sub.last_check + self.settings.interval_h * 3600)
            if sub.last_check else 0.0, "last_error": sub.last_error, "last_found": sub.last_found,
            "pending": len(sub.pending), "files": len(sub.files), "failed": sub.failed,
            "downloading": sum(1 for v in self.inflight.values() if v == sub.id),
            "pending_titles": [e.get("title") or e.get("url") for e in sub.pending[:20]],
            "file_names": [Path(f["path"]).name for f in sub.files[-20:]],
        }

    def state(self) -> dict[str, Any]:
        s = self.settings
        now = self.clock()
        window = parse_window(s.window)
        allowed, reason = gate(now, window, self.store.quota, s.max_items, s.max_mb, self.metered, s.pause_metered)
        return {"metered": self.metered, "allowed": allowed, "reason": reason, "window": s.window,
                "window_text": format_window(window), "next_open": next_open(now, window).timestamp(),
                "quota": self.store.quota.to_dict(), "inflight": len(self.inflight),
                "chains": self.chain.running(), "subscriptions": len(self.store.subs)}

    def listing(self) -> dict[str, Any]:
        subs = sorted(self.store.subs.values(), key=lambda s: s.title.casefold())
        return {"subscriptions": [self.public(s) for s in subs], "settings": self.settings.to_dict(),
                "state": self.state(), "presets": [{"id": p["id"], "title": p["title"]} for p in PRESETS
                                                   if p["id"] in PRESET_IDS],
                "rename_presets": list(RENAME_PRESETS)}

    def get(self, sid: str) -> Subscription:
        sub = self.store.subs.get(sid)
        if sub is None:
            raise RpcError(NOT_FOUND, f"no existe la suscripción {sid}")
        return sub

    # -- detection and adding -------------------------------------------------------------------------------------

    async def detect(self, url: str) -> dict[str, Any]:
        try:
            kind, norm = detect_mod.classify_url(url)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        if kind in ("channel", "playlist"):
            data = await self._list(norm, kind, 5)
            return {"kind": kind, "url": norm, "title": self._title_of(data, kind), "entries": data["entries"][:5]}
        if kind in (None, "rss?", "rss"):
            try:
                is_feed, _ctype = await asyncio.to_thread(detect_mod.sniff, norm)
            except OSError as exc:
                if kind == "rss?":
                    raise RpcError(UNAVAILABLE, str(exc)) from None
                is_feed = False
            if is_feed:
                feed = await self._fetch_feed(norm, force=True)
                return {"kind": "rss", "url": norm, "title": feed.title or norm,
                        "entries": [{"id": e.id, "title": e.title, "url": e.url} for e in feed.newest_first()[:5]]}
        try:
            data = await self.ytdl.list_entries(norm, 5)
        except RpcError as exc:
            raise RpcError(UNAVAILABLE, "no es un canal, una lista ni un podcast: " + exc.message) from None
        if data.get("raw_type") != "playlist":
            raise RpcError(INVALID_PARAMS, "es un vídeo suelto, no un canal ni una lista (usa «Descargar»)")
        return {"kind": "playlist", "url": norm, "title": self._title_of(data, "playlist"),
                "entries": data["entries"][:5]}

    @staticmethod
    def _title_of(data: dict[str, Any], kind: str) -> str:
        title = str(data.get("channel") or data.get("uploader") or data.get("title") or "") if kind == "channel" \
            else str(data.get("title") or "")
        return title.removesuffix(" - Videos").strip() or str(data.get("webpage_url") or "")

    async def add(self, url: str, title: str | None = None, rules: dict[str, Any] | None = None,
                  check: bool = True) -> Subscription:
        info = await self.detect(url)
        for s in self.store.subs.values():
            if s.url == info["url"]:
                raise RpcError(INVALID_PARAMS, f"ya estás suscrito: {s.title}")
        kind = info["kind"]
        sub = Subscription(url=info["url"], kind=kind, title=(title or info["title"] or info["url"]).strip(),
                           preset="audio_original" if kind == "rss" else "video_1080",
                           initial=DEFAULT_INITIAL[kind])
        try:
            sub.update(dict(rules or {}))
        except (TypeError, ValueError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        if not sub.folder:
            base = self.downloads.settings.resolved_dir("audio" if sub.is_audio() else "video")
            sub.folder = str(base / "Suscripciones" / safe_component(sub.title))
        self.store.subs[sub.id] = sub
        self.store.save()
        self._push(sub)
        if check:
            self.check_later(sub, manual=True)
        return sub

    def update(self, sid: str, changes: dict[str, Any]) -> Subscription:
        sub = self.get(sid)
        try:
            sub.update(changes)
        except (TypeError, ValueError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        self.store.save()
        self._push(sub)
        self.schedule_dispatch()
        return sub

    def remove(self, sid: str) -> bool:
        """Forget a subscription (its files stay; queued downloads of it are cancelled)."""
        sub = self.get(sid)
        for did, s in list(self.inflight.items()):
            if s == sid:
                self.inflight.pop(did, None)
                self.downloads.cancel(did)
        self.store.subs.pop(sub.id, None)
        self.store.save()
        self._push()
        return True

    # -- checking -------------------------------------------------------------------------------------------------

    def check_later(self, sub: Subscription, manual: bool = False) -> Any:
        job = self.checks.get(sub.id)
        if job is not None and job.status in (Status.QUEUED, Status.RUNNING):
            return job

        async def body(job: Any) -> dict[str, Any]:
            return await self.check(sub)

        job = self.server.jobs.submit(f"feeds.check:{sub.id}", body,
                                      priority=Priority.INTERACTIVE if manual else Priority.INDEX, heavy=False,
                                      meta={"feed": sub.id})
        self.checks[sub.id] = job
        self._push(sub)
        return job

    async def _list(self, url: str, kind: str, limit: int) -> dict[str, Any]:
        return await self.ytdl.list_entries(url, limit)

    async def _fetch_feed(self, url: str, force: bool = False):  # noqa: ANN202 - Feed
        try:
            res = await asyncio.to_thread(self.http.fetch, url, 0.0 if force else 300.0)
            return parse_feed(await asyncio.to_thread(res.read_bytes))
        except FeedError as exc:
            raise RpcError(UNAVAILABLE, f"el podcast no se puede leer: {exc}") from None
        except Exception as exc:  # noqa: BLE001 - FetchError, OSError
            raise RpcError(UNAVAILABLE, str(exc)) from None

    async def entries_of(self, sub: Subscription) -> list[dict[str, Any]]:
        """Newest first, as dicts {id, title, url, published, duration, direct}."""
        if sub.kind == "rss":
            feed = await self._fetch_feed(sub.url, force=True)
            if feed.title and not sub.title:
                sub.title = feed.title
            return [{"id": e.id, "title": e.title, "url": e.url, "published": e.published, "duration": e.duration,
                     "direct": e.direct} for e in feed.newest_first()]
        limit = self.settings.scan_depth if sub.kind == "channel" else PLAYLIST_MAX
        data = await self._list(sub.url, sub.kind, limit)
        return [{"id": str(e.get("id") or e.get("url")), "title": e.get("title") or e.get("url"), "url": e.get("url"),
                 "published": None, "duration": e.get("duration"), "direct": False}
                for e in data["entries"] if e.get("url") and e.get("live_status") not in ("is_live", "is_upcoming")]

    async def check(self, sub: Subscription) -> dict[str, Any]:
        try:
            entries = await self.entries_of(sub)
        except RpcError as exc:
            sub.last_error, sub.last_check = exc.message, time.time()
            self.store.save()
            self._push(sub)
            raise
        busy = {e.get("id") for e in sub.pending} | {
            (self.downloads.items[d].spec.extra.get("entry") or {}).get("id")
            for d, s in self.inflight.items() if s == sub.id and d in self.downloads.items}
        new = [e for e in entries if e["id"] not in sub.seen and e["id"] not in busy]
        take = new
        if not sub.checked:
            # first check: the «initial» newest (-1 = all of them); everything else counts as already seen
            take = new if sub.initial < 0 else new[: sub.initial]
            for e in new[len(take):] if sub.initial >= 0 else []:
                sub.mark_seen(e["id"])
            sub.checked = True
        if sub.kind == "rss":
            take = sorted(take, key=lambda e: e.get("published") or 0.0)    # oldest first
        elif sub.kind == "channel":
            take = list(reversed(take))                                      # the channel lists newest first
        for e in take:
            sub.pending.append({**e, "attempts": 0})
        sub.last_found, sub.last_check, sub.last_error = len(take), time.time(), ""
        self.store.save()
        self._push(sub)
        self.schedule_dispatch()
        return {"found": len(take), "pending": len(sub.pending), "entries": len(entries)}

    # -- downloading ----------------------------------------------------------------------------------------------

    def schedule_dispatch(self) -> None:
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            if self._dispatch_handle is None or self._dispatch_handle.cancelled():
                self._dispatch_handle = loop.call_soon(self._dispatch_soon)

    def _dispatch_soon(self) -> None:
        self._dispatch_handle = None
        self.dispatch()

    def dispatch(self) -> int:
        """Hand pending entries to the download manager while the rules allow it; returns how many started."""
        if self._closing:
            return 0
        s = self.settings
        allowed, reason = gate(self.clock(), parse_window(s.window), self.store.quota, s.max_items, s.max_mb,
                               self.metered, s.pause_metered)
        changed = reason != self.reason
        self.reason = reason
        if not allowed:
            if s.pause_metered and self.metered:
                self._pause_running()
            if changed:
                self._push()
            return 0
        started = 0
        while len(self.inflight) < max(1, s.parallel):
            subs = [x for x in self.store.subs.values() if x.pending and not x.paused]
            if not subs:
                break
            # the subscription that waited longest for its turn goes first (round robin over subscriptions)
            sub = min(subs, key=lambda x: (x.files[-1]["at"] if x.files else 0.0, x.created_at))
            entry = sub.pending.pop(0)
            try:
                self._submit(sub, entry)
                started += 1
            except (RpcError, ValueError) as exc:
                sub.last_error = getattr(exc, "message", None) or str(exc)
                sub.failed += 1
                sub.mark_seen(entry.get("id", ""))
            self.store.save()
            self._push(sub)
        if changed:
            self._push()
        return started

    def _submit(self, sub: Subscription, entry: dict[str, Any]) -> DownloadItem:
        options: dict[str, Any] = {}
        if sub.container:
            options["container"] = sub.container
        if sub.sponsorblock:
            options["sponsorblock"] = sub.sponsorblock
        spec = self.ytdl.spec_from_params({"url": entry["url"], "preset": sub.preset, "options": options})
        spec.playlist = False
        spec.archive = sub.kind != "rss"     # yt-dlp's archive knows video ids; a podcast file has none worth keeping
        spec.title = entry.get("title") or None
        extra = {**spec.extra, "feed": sub.id, "feed_title": sub.title, "entry": entry, "entry_title": entry.get("title"),
                 "published": entry.get("published"), "priority": "low"}
        if sub.kind == "rss":
            date = dt.datetime.fromtimestamp(entry["published"]).strftime("%Y-%m-%d ") if entry.get("published") else ""
            name = safe_component(f"{date}- {entry.get('title') or entry.get('id')}" if date
                                  else str(entry.get("title") or entry.get("id")))
            extra["template"] = name.replace("%", "%%") + ".%(ext)s"
        spec.extra = extra
        item = self.downloads.submit(spec, title=entry.get("title") or "", notify="mu_ytdl", out_dir=sub.folder)
        self.inflight[item.id] = sub.id
        return item

    def _pause_running(self) -> None:
        for did in list(self.inflight):
            item = self.downloads.get(did)
            if item is not None and item.status not in FINAL:
                self._pausing.add(did)
                self.downloads.cancel(did)

    def _final(self, item: DownloadItem) -> None:
        """Download manager hook (a download reached done / failed / cancelled)."""
        sid = item.spec.extra.get("feed")
        if not sid:
            if item.status == "done" and self.settings.chain_downloads:
                cfg = ChainConfig.from_dict(self.settings.chain)
                if cfg.active():
                    self.chain.start(item, cfg)
            return
        self.inflight.pop(item.id, None)
        sub = self.store.subs.get(sid)
        entry = dict(item.spec.extra.get("entry") or {})
        paused = item.id in self._pausing
        self._pausing.discard(item.id)
        if sub is None:
            return
        if item.status == "done":
            sub.mark_seen(entry.get("id", ""))
            size = 0
            for o in item.outputs:
                with contextlib.suppress(OSError):
                    size += os.path.getsize(o)
            self.store.quota.items += 1
            self.store.quota.bytes += size or int(item.total or 0)
            self.chain.start(item, self.chain_of(sub))   # records the file when it is over (even with no steps)
        elif item.status == "cancelled" and (paused or self._closing):
            sub.pending.insert(0, entry)                 # yt-dlp --continue picks the .part up next time
        elif item.status == "cancelled":
            sub.mark_seen(entry.get("id", ""))            # the user cancelled it: not again
        else:
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            sub.last_error = item.error
            if "already been recorded in the archive" in (item.error or "") or "sin producir" in (item.error or ""):
                sub.mark_seen(entry.get("id", ""))       # downloaded before (another subscription, a list download)
            elif entry["attempts"] < MAX_ATTEMPTS:
                sub.pending.append(entry)
            else:
                sub.failed += 1
                sub.mark_seen(entry.get("id", ""))
        self.store.save()
        self._push(sub)
        self.schedule_dispatch()

    def _chain_for(self, item: DownloadItem) -> ChainConfig | None:
        sid = item.spec.extra.get("feed")
        if sid and sid in self.store.subs:
            return self.chain_of(self.store.subs[sid])
        if self.settings.chain_downloads:
            return ChainConfig.from_dict(self.settings.chain)
        return None

    async def _chain_done(self, item: DownloadItem) -> None:
        sid = item.spec.extra.get("feed")
        sub = self.store.subs.get(sid or "")
        if sub is None:
            return
        main = (item.post or {}).get("file") or ""
        if not main or not Path(main).is_file():
            return
        entry = item.spec.extra.get("entry") or {}
        extra = [o for o in [*item.outputs, *((item.post or {}).get("outputs") or [])] if o != main]
        key = ""
        watch = getattr(self.server, "watch", None)
        if watch is not None:
            with contextlib.suppress(Exception):
                key = await watch.key_for(main)
        sub.files = [f for f in sub.files if f.get("path") != main]
        sub.files.append({"id": entry.get("id", ""), "title": entry.get("title") or item.title, "path": main,
                          "extra": list(dict.fromkeys(extra)), "at": time.time(), "published": entry.get("published"),
                          "key": key, "download": item.id})
        self.store.save()
        await self.apply_rules(sub)
        self._push(sub)

    # -- rules: keep N, delete what was watched ---------------------------------------------------------------------

    def _watched(self, rec: dict[str, Any], grace: float) -> bool:
        watch = getattr(self.server, "watch", None)
        if watch is None or not rec.get("key"):
            return False
        row = watch.store.get(rec["key"])
        return bool(row and row.get("finished") and time.time() - float(row.get("updated_at") or 0) >= grace)

    def _delete(self, sub: Subscription, rec: dict[str, Any]) -> int:
        roots = self.roots(sub)
        n = 0
        for p in [rec.get("path"), *(rec.get("extra") or [])]:
            if not p:
                continue
            path = Path(p)
            if not inside(path, roots):
                log.warning("feeds: %s is outside the folders of %s: not deleted", path, sub.title)
                continue
            if path.is_file() and not path.is_symlink():
                with contextlib.suppress(OSError):
                    path.unlink()
                    n += 1
        return n

    async def apply_rules(self, sub: Subscription) -> dict[str, int]:
        """«Borrar lo visto» and «conservar N» for one subscription (files it downloaded, inside its folders)."""
        def work() -> dict[str, int]:
            removed = 0
            alive = [f for f in sub.files if f.get("path") and Path(f["path"]).is_file()]
            gone = len(sub.files) - len(alive)
            grace = self.settings.watched_grace_h * 3600
            if sub.delete_watched:
                keep = []
                for f in alive:
                    # the record only goes away when the file really did: otherwise a file that could not be deleted
                    # was forgotten and stayed out of the rules for ever, with nothing said
                    if self._watched(f, grace) and self._delete(sub, f) > 0:
                        removed += 1
                    else:
                        keep.append(f)
                alive = keep
            if sub.keep > 0 and len(alive) > sub.keep:
                order = sorted(alive, key=lambda f: (f.get("published") or f.get("at") or 0.0))
                extra_n = len(alive) - sub.keep
                drop = []
                for f in order:
                    if len(drop) >= extra_n:
                        break
                    if not sub.keep_watched_only or self._watched(f, 0.0):
                        drop.append(f)
                deleted = []
                for f in drop:
                    if self._delete(sub, f) > 0:
                        removed += 1
                        deleted.append(f)
                alive = [f for f in alive if f not in deleted]
            sub.files = alive
            return {"removed": removed, "gone": gone, "files": len(alive)}

        res = work()   # a few stat() calls, one SQLite lookup per file and the deletions: no thread (no races)
        if res["removed"] or res["gone"]:
            self.store.save()
            self._push(sub)
        return res


def register(server: MpvdServer, service: FeedsService) -> None:  # noqa: C901 - flat list of handlers
    d = server.dispatcher
    server.services["feeds"] = True

    @d.method("feeds.list")
    async def listing(ctx: RpcContext) -> dict[str, Any]:
        """Subscriptions, global settings and the current state (metered, window, limit)."""
        await service.refresh_metered()
        return service.listing()

    @d.method("feeds.detect")
    async def detect(ctx: RpcContext, url: str) -> dict[str, Any]:
        """What a URL is: channel, list or podcast (RSS), with its title and newest entries."""
        return await service.detect(url)

    @d.method("feeds.add")
    async def add(ctx: RpcContext, url: str, title: str | None = None, rules: dict[str, Any] | None = None,
                  check: bool = True) -> dict[str, Any]:
        """Subscribe (type detected); the first check runs right away and takes the ``initial`` newest entries."""
        return service.public(await service.add(url, title, rules, check))

    @d.method("feeds.get")
    async def get(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """One subscription with its pending entries and recorded files."""
        sub = service.get(id)
        return {**service.public(sub), "pending_entries": sub.pending, "file_records": sub.files}

    @d.method("feeds.update")
    async def update(ctx: RpcContext, id: str, **changes: Any) -> dict[str, Any]:  # noqa: A002
        """Change rules: preset, container, sponsorblock, keep, keep_watched_only, delete_watched, initial, title,
        folder, paused, chain ({loudnorm, subtitles, translate, rename, move_to}, or null / "global" = the global one)."""
        return service.public(service.update(id, changes))

    @d.method("feeds.pause")
    async def pause(ctx: RpcContext, id: str, paused: bool = True) -> dict[str, Any]:  # noqa: A002
        """Pause or resume a subscription (no checks, no downloads)."""
        return service.public(service.update(id, {"paused": paused}))

    @d.method("feeds.remove")
    async def remove(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Forget a subscription; its files are kept."""
        return {"removed": service.remove(id)}

    @d.method("feeds.check")
    async def check(ctx: RpcContext, id: str | None = None, wait: bool = False) -> dict[str, Any]:  # noqa: A002
        """«Comprobar ahora»: one subscription or all (also paused ones when named). ``wait`` returns the results."""
        subs = [service.get(id)] if id else [s for s in service.store.subs.values() if not s.paused]
        await service.refresh_metered(force=True)
        jobs = [service.check_later(s, manual=True) for s in subs]
        if not wait:
            return {"jobs": [j.id for j in jobs]}
        results = {}
        for s, j in zip(subs, jobs, strict=True):
            await j.wait()
            results[s.id] = j.result if j.status == Status.DONE else {"error": j.error}
        return {"results": results, "state": service.state()}

    @d.method("feeds.rules.apply")
    async def rules_apply(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Apply «conservar N» and «borrar lo visto» now."""
        return await service.apply_rules(service.get(id))

    @d.method("feeds.dispatch")
    async def dispatch(ctx: RpcContext) -> dict[str, Any]:
        """Start pending downloads now if the rules allow it (they also start by themselves every minute)."""
        await service.refresh_metered(force=True)
        return {"started": service.dispatch(), "state": service.state()}

    @d.method("feeds.settings.get")
    async def settings_get(ctx: RpcContext) -> dict[str, Any]:
        """Interval, window, limit per window, metered pause, keep running and the default chain."""
        return service.settings.to_dict()

    @d.method("feeds.settings.set")
    async def settings_set(ctx: RpcContext, **values: Any) -> dict[str, Any]:
        """Change global settings (``window``: «de 1:00 a 7:00», «siempre»…; ``chain``: partial dict)."""
        try:
            service.settings.update(values)
        except (TypeError, ValueError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        service.store.save()
        service.schedule_dispatch()
        service._push()  # noqa: SLF001
        return service.settings.to_dict()

    @d.method("feeds.state")
    async def state(ctx: RpcContext) -> dict[str, Any]:
        """Metered connection, window, limit spent and why nothing downloads (``reason``)."""
        await service.refresh_metered(force=True)
        return service.state()

    @d.method("feeds.chain.status")
    async def chain_status(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """The post-download steps of one download."""
        item = service.downloads.get(id)
        if item is None:
            raise RpcError(NOT_FOUND, f"unknown download: {id}")
        return {"id": item.id, "status": item.status, "message": item.message, "post": item.post,
                "outputs": item.outputs}


__all__ = ["FeedsService", "inside", "period_start", "register"]
