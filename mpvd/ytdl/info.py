"""Analysis of yt-dlp ``-J`` output: normalized format rows grouped for the quality menu, media summary, flat playlists.

Facts verified on real fixtures (docs/YTDLP.md §8): YouTube has no combined video+audio formats any more; ``vcodec``/``acodec``
are ``"none"`` when absent but may be ``null`` on other extractors (archive.org); storyboards use ``protocol: "mhtml"``.
"""

from __future__ import annotations

from typing import Any

KIND_COMBINED = "combined"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KINDS = (KIND_COMBINED, KIND_VIDEO, KIND_AUDIO)


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def human_size(n: float | None) -> str:
    if n is None or n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def human_bitrate(kbps: float | None) -> str:
    if kbps is None or kbps <= 0:
        return ""
    return f"{kbps / 1000:.1f} Mbps" if kbps >= 1000 else f"{kbps:.0f} kbps"


def short_codec(codec: Any) -> str:
    if not codec or codec == "none":
        return ""
    c = str(codec).lower()
    for prefix, name in (("avc1", "H.264"), ("avc3", "H.264"), ("h264", "H.264"), ("hev1", "H.265"), ("hvc1", "H.265"),
                         ("h265", "H.265"), ("av01", "AV1"), ("av1", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"), ("vp8", "VP8"),
                         ("mp4a.40.2", "AAC"), ("mp4a.40.5", "AAC-HE"), ("mp4a", "AAC"), ("aac", "AAC"), ("opus", "Opus"),
                         ("vorbis", "Vorbis"), ("mp3", "MP3"), ("ec-3", "E-AC-3"), ("ac-3", "AC-3"), ("flac", "FLAC"),
                         ("theora", "Theora"), ("mpeg4", "MPEG-4")):
        if c.startswith(prefix):
            return name
    return c.split(".")[0].upper()


def is_storyboard(f: dict[str, Any]) -> bool:
    return f.get("protocol") == "mhtml" or f.get("vcodec") == "images" or f.get("format_note") == "storyboard"


def classify(f: dict[str, Any]) -> str:
    v, a = f.get("vcodec"), f.get("acodec")
    has_dims = any(f.get(k) for k in ("width", "height", "fps"))
    has_v = v != "none" and (v is not None or has_dims)
    has_a = a != "none"  # null = unknown → assume present (archive.org, HLS audio without codec info)
    if has_v and has_a:
        return KIND_COMBINED
    return KIND_VIDEO if has_v else KIND_AUDIO


def format_row(f: dict[str, Any]) -> dict[str, Any]:
    kind = classify(f)
    height, width, fps = _int(f.get("height")), _int(f.get("width")), _num(f.get("fps"))
    tbr, vbr, abr = _num(f.get("tbr")), _num(f.get("vbr")), _num(f.get("abr"))
    size = _num(f.get("filesize"))
    approx = False
    if size is None:
        size = _num(f.get("filesize_approx"))
        approx = size is not None
    hdr = str(f.get("dynamic_range") or "").upper().startswith("HDR")
    vcodec, acodec = short_codec(f.get("vcodec")), short_codec(f.get("acodec"))
    parts: list[str] = []
    if kind != KIND_AUDIO:
        if height:
            res = f"{height}p"
            if fps and fps > 31:
                res += f"{fps:.0f}"
            parts.append(res)
        elif width:
            parts.append(f"{width}w")
        if hdr:
            parts.append("HDR")
        if vcodec:
            parts.append(vcodec)
    if kind != KIND_VIDEO:
        if acodec:
            parts.append(acodec)
        if abr:
            parts.append(f"{abr:.0f}k")
        elif kind == KIND_AUDIO and tbr:
            parts.append(f"{tbr:.0f}k")
        ch = _int(f.get("audio_channels"))
        if ch and ch > 2:
            parts.append(f"{ch}ch")
    ext = str(f.get("ext") or "")
    if ext:
        parts.append(ext)
    note = str(f.get("format_note") or "")
    if note and note.lower() not in ("default", "medium", "low", "high", "tiny", "storyboard") and not note[:1].isdigit():
        parts.append(note)
    hints: list[str] = []
    if size:
        hints.append(("~" if approx else "") + human_size(size))
    br = human_bitrate(tbr or ((vbr or 0) + (abr or 0)) or None)
    if br:
        hints.append(br)
    lang = f.get("language")
    if lang and kind != KIND_VIDEO:
        hints.append(str(lang))
    if f.get("protocol") in ("m3u8", "m3u8_native"):
        hints.append("HLS")
    elif f.get("protocol") == "http_dash_segments":
        hints.append("DASH")
    if f.get("has_drm"):
        hints.append("DRM")
    return {
        "id": str(f.get("format_id")), "kind": kind, "ext": ext, "container": f.get("container"),
        "vcodec": f.get("vcodec"), "acodec": f.get("acodec"), "vcodec_name": vcodec, "acodec_name": acodec,
        "width": width, "height": height, "fps": fps, "hdr": hdr,
        "tbr": tbr, "vbr": vbr, "abr": abr, "asr": _int(f.get("asr")), "channels": _int(f.get("audio_channels")),
        "filesize": size, "filesize_approx": approx, "protocol": f.get("protocol"), "language": lang,
        "note": note, "has_drm": bool(f.get("has_drm")), "quality": _num(f.get("quality")),
        "label": " · ".join(parts) or str(f.get("format") or f.get("format_id")),
        "hint": " · ".join(hints),
    }


def _sort_key(row: dict[str, Any]) -> tuple[float, ...]:
    return (-(row["height"] or 0), -(row["fps"] or 0), -(row["tbr"] or row["abr"] or 0), -(row["quality"] or 0))


def group_formats(info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """All non-storyboard formats as rows, grouped by kind, best first, with counts."""
    groups: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
    for f in info.get("formats") or []:
        if not isinstance(f, dict) or is_storyboard(f):
            continue
        row = format_row(f)
        groups[row["kind"]].append(row)
    for rows in groups.values():
        rows.sort(key=_sort_key)
    return groups


def selected_format_ids(info: dict[str, Any]) -> list[str]:
    """Format ids yt-dlp picked for the default ``-f`` (``requested_formats`` or ``format_id`` ``a+b``)."""
    req = info.get("requested_formats")
    if isinstance(req, list) and req:
        return [str(f.get("format_id")) for f in req if isinstance(f, dict)]
    fid = info.get("format_id")
    return str(fid).split("+") if fid else []


def summary(info: dict[str, Any]) -> dict[str, Any]:
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    chapters = info.get("chapters") or []
    thumb = info.get("thumbnail")
    if not thumb:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            thumb = sorted(thumbs, key=lambda t: _num(t.get("preference")) or -999)[-1].get("url")
    return {
        "id": info.get("id"), "title": info.get("title"), "duration": _num(info.get("duration")),
        "uploader": info.get("uploader") or info.get("channel"), "channel": info.get("channel"),
        "webpage_url": info.get("webpage_url") or info.get("original_url"), "extractor": info.get("extractor"),
        "thumbnail": thumb, "is_live": bool(info.get("is_live")), "live_status": info.get("live_status"),
        "upload_date": info.get("upload_date"), "view_count": _int(info.get("view_count")),
        "chapters": len(chapters) if isinstance(chapters, list) else 0,
        "subtitles": sorted(subs.keys()) if isinstance(subs, dict) else [],
        "automatic_captions": sorted(autos.keys()) if isinstance(autos, dict) else [],
        "selected": selected_format_ids(info),
        "type": info.get("_type") or "video",
        "playlist_count": _int(info.get("playlist_count")),
    }


def analyze(info: dict[str, Any]) -> dict[str, Any]:
    groups = group_formats(info)
    return {
        **summary(info),
        "formats": groups,
        "counts": {k: len(v) for k, v in groups.items()},
        "has_combined": bool(groups[KIND_COMBINED]),
    }


def flat_entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Entries of a ``--flat-playlist -J`` result as compact rows."""
    rows = []
    for e in info.get("entries") or []:
        if not isinstance(e, dict):
            continue
        rows.append({
            "id": e.get("id"), "title": e.get("title"), "url": e.get("url") or e.get("webpage_url"),
            "duration": _num(e.get("duration")), "uploader": e.get("uploader") or e.get("channel"),
            "live_status": e.get("live_status"), "view_count": _int(e.get("view_count")),
        })
    return rows
