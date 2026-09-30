#!/usr/bin/env python3
"""Stand-in for yt-dlp used by the tests (no network): replays the recorded ``-J`` fixtures, "downloads" a local
file with the same MU_PROGRESS/MU_PP/MU_DONE lines as the real binary and records every argv it receives.

Environment:
  FAKE_YTDLP_ARGLOG   append one JSON line per invocation (argv) to this file
  FAKE_YTDLP_MEDIA    directory with test media (tests/fixtures/media); used for the fake.test URLs and as download source
  FAKE_YTDLP_MEDIA_URL  base URL serving that directory over HTTP (ytdl_hook refuses non-URL "streams")
  FAKE_YTDLP_DELAY    seconds between progress lines (default 0.05)
  FAKE_YTDLP_STEPS    number of progress lines per file (default 6)
  FAKE_YTDLP_VERSION  what --version prints (default 2026.08.19)
  FAKE_YTDLP_SEARCH_DELAY  seconds a synthetic "fake" search takes (default 0; lets tests see the loading state)

URL conventions: ``*fail*`` → exit 1 with an ERROR line; ``youtube.com/watch`` → youtube_bbb.json; ``archive.org`` →
archive_countdown.json; ``ytsearch`` → playlist_flat_search.json, except queries containing "fake", which get
synthetic results pointing at ``https://fake.test/…`` (one of them live); ``/playlist``/``@`` → playlist_flat.json;
``https://fake.test/<name>`` → synthetic info whose format URLs point at local media (for mpv's ytdl_hook).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WITH_VALUE = {
    "-f", "--format", "-P", "--paths", "-o", "--output", "--progress-template", "--print", "--audio-format",
    "--audio-quality", "--merge-output-format", "--remux-video", "--sub-langs", "--sub-format", "--js-runtimes",
    "--ffmpeg-location", "--retries", "-R", "--socket-timeout", "--progress-delta", "--playlist-items", "-I",
    "--color", "--sponsorblock-mark", "--sponsorblock-remove", "--convert-subs", "--extractor-args",
    "--remote-components", "--cookies", "--cookies-from-browser", "-S", "--format-sort", "--user-agent",
    "--sub-langs", "--ytdl-format", "--download-sections",
}


def parse(argv: list[str]) -> tuple[dict[str, list[str]], set[str], list[str]]:
    opts: dict[str, list[str]] = {}
    flags: set[str] = set()
    urls: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            urls += argv[i + 1:]
            break
        if a.startswith("-") and len(a) > 1:
            if "=" in a and a.startswith("--"):
                k, v = a.split("=", 1)
                opts.setdefault(k, []).append(v)
            elif a in WITH_VALUE:
                opts.setdefault(a, []).append(argv[i + 1] if i + 1 < len(argv) else "")
                i += 1
            else:
                flags.add(a)
        else:
            urls.append(a)
        i += 1
    return opts, flags, urls


def first(opts: dict[str, list[str]], *names: str, default: str = "") -> str:
    for n in names:
        if opts.get(n):
            return opts[n][0]
    return default


def fake_info(url: str, fmt: str) -> dict:
    media = Path(os.environ.get("FAKE_YTDLP_MEDIA", str(HERE.parents[0] / "media")))
    base = os.environ.get("FAKE_YTDLP_MEDIA_URL", "").rstrip("/")

    def media_url(fname: str) -> str:
        return f"{base}/{fname}" if base else str(media / fname)

    name = url.rstrip("/").rsplit("/", 1)[-1] or "video"
    formats = [
        {"format_id": "18", "ext": "mkv", "vcodec": "avc1.64001e", "acodec": "mp4a.40.2", "width": 320, "height": 180,
         "fps": 25, "tbr": 500, "filesize": 200000, "url": media_url("video30.mkv"), "protocol": "https",
         "format_note": "180p", "resolution": "320x180"},
        {"format_id": "137", "ext": "mkv", "vcodec": "avc1.640028", "acodec": "none", "width": 320, "height": 180,
         "fps": 25, "tbr": 400, "filesize": 150000, "url": media_url("chapters.mkv"), "protocol": "https",
         "format_note": "180p", "resolution": "320x180"},
        {"format_id": "140", "ext": "flac", "vcodec": "none", "acodec": "flac", "abr": 128, "tbr": 128, "asr": 16000,
         "audio_channels": 1, "filesize": 90000, "url": media_url("voz_es.flac"), "protocol": "https",
         "format_note": "medium", "resolution": "audio only"},
        {"format_id": "139", "ext": "flac", "vcodec": "none", "acodec": "flac", "abr": 48, "tbr": 48, "asr": 16000,
         "audio_channels": 1, "filesize": 40000, "url": media_url("voz_en.flac"), "protocol": "https",
         "format_note": "low", "resolution": "audio only", "language": "en"},
    ]
    by_id = {f["format_id"]: f for f in formats}
    info = {
        "id": "fake-" + name, "title": "Fake Test Video " + name, "duration": 30, "extractor": "fake",
        "extractor_key": "Fake", "webpage_url": url, "original_url": url, "uploader": "MPV-UOS tests",
        "thumbnail": None, "thumbnails": [], "chapters": None, "subtitles": {}, "automatic_captions": {},
        "live_status": "not_live", "is_live": False, "formats": formats, "_type": "video",
    }
    # Format selection (subset of yt-dlp's language): explicit ids, "a+b", "ba"/"bestaudio", else combined best.
    chosen: list[dict]
    head = fmt.split("/")[0].strip() if fmt else ""
    ids = re.findall(r"^\d+(?:\+\d+)*$", head)
    if ids:
        chosen = [by_id[i] for i in head.split("+") if i in by_id]
    elif "+" in head:
        chosen = [by_id["137"], by_id["140"]]
    elif head.startswith(("ba", "bestaudio")):
        chosen = [by_id["140"]]
    else:
        chosen = [by_id["18"]]
    if not chosen:
        sys.stderr.write(f"ERROR: [fake] {name}: Requested format is not available\n")
        sys.exit(1)
    if len(chosen) == 1:
        f = chosen[0]
        info.update({k: f.get(k) for k in ("url", "ext", "vcodec", "acodec", "width", "height", "fps", "abr", "tbr",
                                            "protocol")})
        info["format_id"] = f["format_id"]
        info["format"] = f["format_id"] + " - " + str(f.get("resolution"))
    else:
        info["requested_formats"] = chosen
        info["format_id"] = "+".join(f["format_id"] for f in chosen)
        info["ext"] = "mkv"
        info["format"] = info["format_id"]
    return info


def fake_search(query: str, n: int) -> dict:
    """Flat ``ytsearchN:`` result shaped like yt-dlp's (entries of ``_type: url``) for the fake.test media."""
    entries = [
        {"_type": "url", "ie_key": "Fake", "id": "fake-a", "url": "https://fake.test/a", "title": "Fake Result A",
         "duration": 30, "channel": "MPV-UOS tests", "uploader": "MPV-UOS tests", "view_count": 1234,
         "live_status": None},
        {"_type": "url", "ie_key": "Fake", "id": "fake-live", "url": "https://fake.test/live",
         "title": "Fake Live B", "duration": None, "channel": "MPV-UOS live", "view_count": None,
         "live_status": "is_live"},
        {"_type": "url", "ie_key": "Fake", "id": "fake-c", "url": "https://fake.test/c", "title": "Fake Result C",
         "duration": 3725, "channel": None, "uploader": "Uploader C", "view_count": 7, "live_status": "not_live"},
        {"_type": "url", "ie_key": "Fake", "id": "no-url", "title": "Entry without URL"},
    ]
    return {"id": query, "title": query, "_type": "playlist", "webpage_url": f"ytsearch{n}:{query}",
            "extractor": "youtube:search", "extractor_key": "YoutubeSearch", "playlist_count": min(n, len(entries)),
            "entries": entries[:n]}


def dump_json(url: str, opts: dict[str, list[str]], flags: set[str]) -> int:
    fmt = first(opts, "-f", "--format")
    nightly = os.environ.get("FAKE_YTDLP_NIGHTLY") == "1" or Path(sys.argv[0]).name.endswith("nightly")
    if "fail" in url and not (nightly and "fail-extract" in url):
        sys.stderr.write("ERROR: [fake] Unsupported URL: " + url + "\n")
        return 1
    if url.startswith("https://fake.test/"):
        print(json.dumps(fake_info(url, fmt)))
        return 0
    if url.startswith("ytsearch"):
        m = re.match(r"ytsearch(\d*):(.*)$", url, re.S)
        if m and "fake" in m.group(2):
            delay = float(os.environ.get("FAKE_YTDLP_SEARCH_DELAY", "0"))
            if delay:
                time.sleep(delay)
            print(json.dumps(fake_search(m.group(2), int(m.group(1) or 1))))
            return 0
        name = "playlist_flat_search.json"
    elif "--flat-playlist" in flags and ("/playlist" in url or "/@" in url):
        name = "playlist_flat.json"
    elif "youtube.com/watch" in url or "youtu.be/" in url:
        name = "youtube_bbb.json"
    elif "archive.org" in url:
        name = "archive_countdown.json"
    else:
        sys.stderr.write("ERROR: [fake] Unsupported URL: " + url + "\n")
        return 1
    sys.stdout.write((HERE / name).read_text(encoding="utf-8").strip() + "\n")
    return 0


def render(template: str, fields: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        return fields.get(m.group(1), m.group(0))

    return re.sub(r"%\((\w+)\)[^a-zA-Z]*[a-zA-Z]", sub, template)


def download(url: str, opts: dict[str, list[str]], flags: set[str]) -> int:
    nightly = os.environ.get("FAKE_YTDLP_NIGHTLY") == "1"   # the "nightly build" fixes "fail-extract" URLs
    if "private" in url:
        sys.stderr.write("ERROR: [fake] " + url + ": Private video. Sign in if you've been granted access\n")
        return 1
    if "fail" in url and not (nightly and "fail-extract" in url):
        sys.stderr.write("WARNING: [fake] something\nERROR: [fake] Unable to download webpage: " + url + "\n")
        return 1
    delay = float(os.environ.get("FAKE_YTDLP_DELAY", "0.05"))
    steps = int(os.environ.get("FAKE_YTDLP_STEPS", "6"))
    media = Path(os.environ.get("FAKE_YTDLP_MEDIA", str(HERE.parents[0] / "media")))
    out_dir = Path(first(opts, "-P", "--paths", default="."))
    template = first(opts, "-o", "--output", default="%(title)s [%(id)s].%(ext)s")
    fmt = first(opts, "-f", "--format", default="bv*+ba/b")
    audio = "-x" in flags or "--extract-audio" in flags
    if audio:
        ext = first(opts, "--audio-format", default="m4a")
        if ext == "best":
            ext = "m4a"
    else:
        # "mp4/mkv" = preference list; the fake pretends the first one is compatible
        ext = first(opts, "--merge-output-format", default="mp4").split("/")[0]
    vid = "fake-" + (url.rstrip("/").rsplit("/", 1)[-1] or "video")
    title = "Fake Test Video"
    # subtitles: one .srt per requested language that exists ("es", "en"; a regex like "en.*" matches), reported by
    # the after_video print like the real binary; --skip-download stops there
    sub_prints = [p.split(":", 1)[1] for p in opts.get("--print", []) if p.startswith("after_video:")]
    written = []
    if "--write-subs" in flags or "--write-auto-subs" in flags:
        wanted = first(opts, "--sub-langs", default="en").split(",")
        out_dir.mkdir(parents=True, exist_ok=True)
        for lang in ("es", "en"):
            if any(w == "all" or re.fullmatch(w, lang) for w in wanted if not w.startswith("-")):
                base = render(template, {"title": title, "id": vid, "ext": "x"})[: -len(".x")]
                sub = out_dir / f"{base}.{lang}.srt"
                sub.write_text(f"1\n00:00:01,000 --> 00:00:03,000\nsubtítulo {lang}\n", encoding="utf-8")
                written.append(str(sub))
        for tpl in sub_prints:
            print(tpl.split(" ", 1)[0] + " " + json.dumps(written), flush=True)
    if "--skip-download" in flags:
        return 0
    files = ["f137." + ("webm" if not audio else "m4a"), "f140.m4a"] if "+" in fmt.split("/")[0] else ["f18." + ext]
    total = 120000
    prog_templates = opts.get("--progress-template", [])
    want_progress = any(t.startswith("download:") for t in prog_templates)
    want_pp = any(t.startswith("postprocess:") for t in prog_templates)
    prints = [p.split(":", 1)[1] for p in opts.get("--print", []) if p.startswith("after_move:")]
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname in files:
        final = str(out_dir / f"{vid}.{fname}")
        for i in range(1, steps + 1):
            if want_progress:
                done = total * i // steps
                d = {"status": "downloading" if i < steps else "finished", "downloaded_bytes": done,
                     "total_bytes": total, "tmpfilename": final + ".part", "filename": final,
                     "eta": (steps - i), "speed": 1_000_000.0, "elapsed": 0.1 * i, "ctx_id": None,
                     "_percent": 100.0 * i / steps}
                print("MU_PROGRESS " + json.dumps(d), flush=True)
            else:
                print(f"[download] {100 * i / steps:5.1f}% of 117.19KiB", flush=True)
            time.sleep(delay)
    if want_pp:
        for pp in (["Merger"] if len(files) > 1 else []) + (["ExtractAudio"] if audio else []) + ["MoveFiles"]:
            print("MU_PP " + json.dumps({"status": "started", "postprocessor": pp}), flush=True)
            print("MU_PP " + json.dumps({"status": "finished", "postprocessor": pp}), flush=True)
    filename = render(template, {"title": title, "id": vid, "ext": ext})
    target = out_dir / filename
    src = media / "video30.mkv"
    if src.is_file():
        shutil.copyfile(src, target)
    else:
        target.write_bytes(os.urandom(total))
    for tpl in prints:
        head = tpl.split(" ", 1)[0]
        print(head + " " + json.dumps({"id": vid, "title": title, "ext": ext, "filepath": str(target),
                                       "format_id": fmt, "duration": 30}), flush=True)
    return 0


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_YTDLP_ARGLOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(argv) + "\n")
    opts, flags, urls = parse(argv)
    if "--version" in flags:
        print(os.environ.get("FAKE_YTDLP_VERSION", "2026.08.19"))
        return 0
    if not urls:
        sys.stderr.write("ERROR: You must provide at least one URL.\n")
        return 2
    if "-J" in flags or "--dump-single-json" in flags or "-j" in flags:
        return dump_json(urls[0], opts, flags)
    return download(urls[0], opts, flags)


if __name__ == "__main__":
    sys.exit(main())
