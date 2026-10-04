"""Download specifications → exact yt-dlp argument lists.

Every option below was verified against ``yt-dlp --help`` of the vendored release (docs/YTDLP.md §5/§9).
"""

from __future__ import annotations

import re

from dataclasses import asdict, dataclass, field
from typing import Any

from mpvd.i18n import t

CONTAINERS = ("mp4", "mkv", "webm")
AUDIO_FORMATS = ("mp3", "opus", "m4a", "flac", "wav")
AUDIO_BITRATES = (96, 128, 160, 192, 256, 320)
SPONSORBLOCK_MODES = ("none", "mark", "remove")
KINDS = ("video", "exact", "audio_original", "audio_convert")

# Container policy. Merging (video+audio) takes a preference list: yt-dlp picks the first container compatible with the
# codecs (get_compatible_ext) and falls back to mkv. Remuxing a single-file format fails when the codecs do not fit the
# target ("If the target container does not support the video/audio codec, remuxing will fail"), so only safe pairs
# are remuxed; mkv accepts anything.
# H31 (ADR-053): mp4 is forced (not "mp4/mkv"): yt-dlp's compatibility table leaves Opus out of mp4 and would fall back
# to .mkv, while ffmpeg muxes AV1/VP9/HEVC/H.264 + Opus/AAC into mp4 fine (verified 2026-09-30: 299+251 → .mp4). A merge
# that still fails (exotic codecs) is retried once as .mkv by the download manager.
MERGE_PREFERENCES = {"mp4": "mp4", "mkv": "mkv", "webm": "webm/mkv"}
REMUX_RULES = {"mp4": "mov>mp4/m4v>mp4/flv>mp4/3gp>mp4", "mkv": "mkv", "webm": ""}
# Format sort (-S) for the resolution presets. yt-dlp's default order ranks AV1/VP9 + Opus first; that pair does not
# fit mp4, so the merge silently fell back to .mkv. "vcodec:h264" = best codec no better than H.264 (H.264 wins over
# AV1/VP9; older codecs such as Theora still qualify when nothing else exists); "res" right after it keeps the highest
# resolution among those (otherwise a combined 360p H.264+AAC format beats a 1080p video-only one on "acodec").
# mkv takes any codec: no -S. Checked with the vendored yt-dlp on the recorded YouTube -J: 360p mp4 → 18 (.mp4),
# best mp4 → 299+140 (1080p .mp4), 360p webm → 243+251 (.webm); archive.org keeps its best ≤360p file.
# H31: mp4 and mkv prefer the best codec this machine decodes in hardware ("vcodec:X", X from mpvd.hwdecode:
# h265 on an Intel iHD with HEVC but no VP9/AV1, h264 when unknown) and the original Opus audio; {vcodec} is filled in.
FORMAT_SORT = {"mp4": "vcodec:{vcodec},res,acodec:opus", "mkv": "vcodec:{vcodec},res,acodec:opus",
               "webm": "vcodec:vp9,res,acodec:opus"}
VCODEC_SORTS = ("av01", "vp9", "h265", "h264")

PROGRESS_PREFIX = "MU_PROGRESS"
POSTPROCESS_PREFIX = "MU_PP"
DONE_PREFIX = "MU_DONE"
SUBS_PREFIX = "MU_SUBS"   # after_video: paths of the subtitle files written (also with --skip-download)
DEFAULT_TEMPLATE = "%(title).120B [%(id)s].%(ext)s"
DEFAULT_SUB_LANGS = "orig,es.*,en.*"   # «orig» = the video's own language (resolved by mpvd, see resolve_sub_langs)
SUBS_MODES = ("embed", "file", "only")  # inside the video · .srt next to it · only the .srt (no video)
SUB_LANG_CHOICES = [  # menu order (H19)
    {"value": "orig,es.*,en.*", "label": "originales + es + en"},
    {"value": "orig", "label": "solo el original"},
    {"value": "es.*", "label": "español"},
    {"value": "en.*", "label": "inglés"},
    {"value": "all,-live_chat", "label": "todos"},
]


@dataclass
class DownloadSpec:
    url: str
    kind: str = "video"                 # video | exact | audio_original | audio_convert
    height: int | None = None           # video: cap (None = best)
    format: str | None = None           # exact: yt-dlp format expression ("137+140", "251", "bv*[height<=720]+ba")
    container: str = "mp4"              # video/exact: mp4 | mkv | webm (merge + remux target)
    audio_format: str = "mp3"           # audio_convert: mp3 | opus | m4a | flac | wav
    audio_bitrate: int | None = 192     # audio_convert (lossy): constant bitrate in kbps; None = VBR
    audio_vbr: int = 0                  # audio_convert with audio_bitrate=None: 0 (best) … 10
    subtitles: bool = False
    sub_langs: str = DEFAULT_SUB_LANGS
    subs_mode: str = "embed"            # embed | file (.srt next to the video) | only (just the .srt)
    chapters: bool = False
    thumbnail: bool = False
    metadata: bool = False
    sponsorblock: str = "none"          # none | mark | remove
    playlist: bool = False              # whole playlist vs only the referenced item
    playlist_items: str | None = None   # e.g. "1:5"
    sections: str | None = None         # time range only: "*10.5-16" (seconds; H18 «Grabar» of internet videos)
    archive: bool = False               # skip what the download archive already has (lists, channels, batches)
    list_folder: bool = False           # a playlist goes to its own folder with numbered files
    title: str | None = None            # display only
    extra: dict[str, Any] = field(default_factory=dict)  # display-only info (preset id...)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DownloadSpec:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        spec = cls(**known)
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not self.url or not isinstance(self.url, str):
            raise ValueError("url required")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.kind == "exact" and not self.format:
            raise ValueError("exact download needs a format")
        if self.container not in CONTAINERS:
            raise ValueError(f"container must be one of {CONTAINERS}")
        if self.audio_format not in AUDIO_FORMATS:
            raise ValueError(f"audio_format must be one of {AUDIO_FORMATS}")
        if self.audio_bitrate is not None and not (8 <= int(self.audio_bitrate) <= 512):
            raise ValueError("audio_bitrate out of range")
        if not (0 <= int(self.audio_vbr) <= 10):
            raise ValueError("audio_vbr must be 0..10")
        if self.sponsorblock not in SPONSORBLOCK_MODES:
            raise ValueError(f"sponsorblock must be one of {SPONSORBLOCK_MODES}")
        if self.height is not None and int(self.height) <= 0:
            raise ValueError("height must be positive")
        if self.subs_mode not in SUBS_MODES:
            raise ValueError(f"subs_mode must be one of {SUBS_MODES}")
        if self.sections is not None and not SECTIONS_RE.match(self.sections):
            raise ValueError("sections must look like *START-END in seconds (END may be inf)")

    @property
    def is_audio(self) -> bool:
        return self.kind in ("audio_original", "audio_convert")

    def format_expression(self) -> str:
        if self.kind == "exact":
            return str(self.format)
        if self.sections:
            # sections are cut by ffmpeg with input seeking + stream copy: with YouTube's webm/opus formats the range
            # comes out wrong (16 s instead of 6, verified 2026-09-30), with H.264/AAC in mp4/m4a it is right (±0.03 s)
            if self.is_audio:
                return "ba[ext=m4a]/ba/b"
            h = f"[height<=?{int(self.height)}]" if self.height else ""
            return f"bv*[vcodec^=avc1]{h}+ba[ext=m4a]/b[ext=mp4]{h}/bv*{h}+ba/b"
        if self.kind == "video":
            if self.height:
                h = int(self.height)
                # best video ≤ h + best audio; else best combined ≤ h; else anything (audio-only sites, odd extractors)
                return f"bv*[height<=?{h}]+ba/b[height<=?{h}]/bv*+ba/b"
            return "bv*+ba/b"
        return "ba/b"  # audio: best audio-only, else best anything (-x extracts the audio)

    def describe(self) -> str:
        if self.kind == "exact":
            return f"formato {self.format} → {self.container}"
        if self.kind == "video":
            return (f"vídeo {self.height}p" if self.height else "vídeo mejor calidad") + f" ({self.container})"
        if self.kind == "audio_original":
            return "audio original (sin recodificar)"
        q = f"{self.audio_bitrate} kbps" if self.audio_bitrate else f"VBR q{self.audio_vbr}"
        if self.audio_format in ("flac", "wav"):
            q = "sin pérdida"
        return f"audio {self.audio_format} {q}"


SECTIONS_RE = re.compile(r"^\*\d+(\.\d+)?-(\d+(\.\d+)?|inf)$")


def section_template(template: str) -> str:
    """The same name with the range, so that two ranges of one video never collide (``--no-overwrites``)."""
    tail = ".%(ext)s"
    base = template[: -len(tail)] if template.endswith(tail) else template
    return base + " [%(section_start)d-%(section_end)d s]" + tail


def output_args(out_dir: str, template: str = DEFAULT_TEMPLATE) -> list[str]:
    return ["-P", out_dir, "-o", template]


def progress_args() -> list[str]:
    return [
        "--newline", "--progress-delta", "0.2",
        "--progress-template", f"download:{PROGRESS_PREFIX} %(progress)j",
        "--progress-template", f"postprocess:{POSTPROCESS_PREFIX} %(progress)j",
        "--print", f"after_move:{DONE_PREFIX} %(.{{id,title,ext,filepath,format_id,duration,upload_date,uploader,channel}})j",
        "--no-simulate",
    ]


def resolve_sub_langs(langs: str, language: str | None) -> str:
    """``orig`` → the video's language (``<lang>.*``) plus YouTube's original-language auto captions (``.*-orig``)."""
    out: list[str] = []
    for tok in [t.strip() for t in langs.split(",") if t.strip()]:
        if tok == "orig":
            if language:
                out.append(f"{language.split('-')[0]}.*")
            out.append(".*-orig")
        else:
            out.append(tok)
    return ",".join(dict.fromkeys(out))


def list_template(template: str) -> str:
    """``<list title>/<NNN> - <name>``: a folder per playlist or channel, files numbered in the list's order."""
    return "%(playlist_title,playlist_id|Lista)s/%(playlist_index)03d - " + template


RATE_RE = re.compile(r"^\d+(\.\d+)?[KMG]?$")


def build_args(spec: DownloadSpec, out_dir: str, template: str = DEFAULT_TEMPLATE, rate_limit: str = "",
               archive_file: str | None = None, vcodec: str = "h264") -> list[str]:
    """Arguments after the binary (+ its base args); the URL is last. ``rate_limit``: yt-dlp ``-r`` (``2M``, ``500K``);
    ``archive_file``: the download archive used when ``spec.archive``; ``vcodec``: preferred codec limit for ``-S``
    (the best one decoded in hardware, see mpvd.hwdecode.sort_codec)."""
    spec.validate()
    if vcodec not in VCODEC_SORTS:
        vcodec = "h264"
    args: list[str] = ["--no-overwrites", "--continue", "--ignore-errors", "--retries", "5", "--socket-timeout", "30"]
    if rate_limit and RATE_RE.match(rate_limit):
        args += ["-r", rate_limit]
    if spec.archive and archive_file:
        args += ["--download-archive", archive_file]
    only_subs = spec.subs_mode == "only"
    if not only_subs:
        args += ["-f", spec.format_expression()]
    if spec.kind == "video" and FORMAT_SORT[spec.container] and not only_subs:
        args += ["-S", FORMAT_SORT[spec.container].format(vcodec=vcodec)]
    if only_subs:
        pass
    elif spec.is_audio:
        args += ["-x"]
        if spec.kind == "audio_convert":
            args += ["--audio-format", spec.audio_format]
            if spec.audio_format not in ("flac", "wav"):
                if spec.audio_bitrate is not None:
                    args += ["--audio-quality", f"{int(spec.audio_bitrate)}K"]
                else:
                    args += ["--audio-quality", str(int(spec.audio_vbr))]
        # audio_original: -x without --audio-format keeps the original codec (FFmpegExtractAudio "best" = stream copy);
        # H31: the original Opus when there is one (YouTube: 251 over the AAC 140)
        if spec.kind == "audio_original":
            args += ["-S", "acodec:opus"]
    else:
        args += ["--merge-output-format", MERGE_PREFERENCES[spec.container]]
        if REMUX_RULES[spec.container]:
            args += ["--remux-video", REMUX_RULES[spec.container]]
    langs = spec.sub_langs.replace("orig,", "").replace(",orig", "") if "orig" in spec.sub_langs.split(",") else \
        spec.sub_langs  # an unresolved «orig» (no info): the other languages
    langs = langs if langs != "orig" else ".*-orig"
    # .srt files that stay on disk: their paths come from an after_video print (after_move never runs without a
    # download); embedded subtitles are deleted once inside the video, so they are not reported
    subs_print = ["--print", f"after_video:{SUBS_PREFIX} %(requested_subtitles.:.filepath)j"]
    if spec.subs_mode == "only":
        args += ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs, "--convert-subs", "srt",
                 *subs_print]
    elif spec.subtitles and spec.subs_mode == "file":
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", langs, "--convert-subs", "srt", *subs_print]
    elif spec.subtitles and not spec.is_audio:
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", langs, "--embed-subs"]
    if spec.chapters:
        args += ["--embed-chapters"]
    if spec.thumbnail:
        args += ["--embed-thumbnail"]
    if spec.metadata:
        args += ["--embed-metadata"]
    if spec.sponsorblock == "mark":
        args += ["--sponsorblock-mark", "all"]
    elif spec.sponsorblock == "remove":
        args += ["--sponsorblock-remove", "default"]
    if spec.playlist:
        args += ["--yes-playlist"]
        if spec.playlist_items:
            args += ["--playlist-items", spec.playlist_items]
        if spec.list_folder:
            template = list_template(template)
    else:
        args += ["--no-playlist"]
    if spec.sections:
        # cut at keyframes, no re-encode (like the local «Grabar»); needs ffmpeg, which is required anyway
        args += ["--download-sections", spec.sections]
        template = section_template(template)
    args += output_args(out_dir, template)
    args += progress_args()
    args += ["--", spec.url]
    return args


# -- presets shown in the "Descargar" menu ------------------------------------------------------------

PRESETS: list[dict[str, Any]] = [
    {"id": "video_best", "title": "Vídeo · mejor calidad", "group": "video", "spec": {"kind": "video", "height": None}},
    {"id": "video_1080", "title": "Vídeo · hasta 1080p (recomendado)", "group": "video",
     "spec": {"kind": "video", "height": 1080}},
    {"id": "video_720", "title": "Vídeo · 720p", "group": "video", "spec": {"kind": "video", "height": 720}},
    {"id": "video_480", "title": "Vídeo · 480p", "group": "video", "spec": {"kind": "video", "height": 480}},
    {"id": "video_360", "title": "Vídeo · 360p", "group": "video", "spec": {"kind": "video", "height": 360}},
    {"id": "audio_original", "title": "Audio · original (sin recodificar, recomendado)", "group": "audio",
     "spec": {"kind": "audio_original"}},
    {"id": "audio_mp3_128", "title": "Audio · MP3 128 kbps", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": 128}},
    {"id": "audio_mp3_192", "title": "Audio · MP3 192 kbps", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": 192}},
    {"id": "audio_mp3_320", "title": "Audio · MP3 320 kbps", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": 320}},
    {"id": "audio_mp3_vbr", "title": "Audio · MP3 VBR (máxima)", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": None, "audio_vbr": 0}},
    {"id": "audio_opus_128", "title": "Audio · Opus 128 kbps", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "opus", "audio_bitrate": 128}},
    {"id": "audio_m4a_192", "title": "Audio · M4A (AAC) 192 kbps", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "m4a", "audio_bitrate": 192}},
    {"id": "audio_flac", "title": "Audio · FLAC (sin pérdida)", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "flac", "audio_bitrate": None}},
    {"id": "subs_only", "title": "Solo subtítulos (SRT)", "group": "subs",
     "spec": {"kind": "video", "subs_mode": "only", "subtitles": True}},
    {"id": "audio_wav", "title": "Audio · WAV", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "wav", "audio_bitrate": None}},
]


def preset_rows() -> list[dict[str, Any]]:
    """Los presets con el nombre en el idioma de quien mira. H49/G8: la tabla está en castellano porque la cadena
    castellana ES la clave; se traduce aquí, que es por donde salen hacia el menú y el panel."""
    out = []
    for p in PRESETS:
        fila = dict(p)
        for campo in ("title", "label", "hint"):
            if fila.get(campo):
                fila[campo] = t(str(fila[campo]))
        out.append(fila)
    return out


def sub_lang_rows() -> list[dict[str, Any]]:
    return [{**r, "label": t(str(r["label"]))} for r in SUB_LANG_CHOICES]


def preset(preset_id: str) -> dict[str, Any]:
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    raise KeyError(preset_id)


def spec_from_preset(preset_id: str, url: str, options: dict[str, Any] | None = None) -> DownloadSpec:
    """Preset + user options (container, sub_langs, toggles) → spec."""
    p = preset(preset_id)
    d: dict[str, Any] = {"url": url, **p["spec"]}
    for k, v in (options or {}).items():
        if k in DownloadSpec.__dataclass_fields__ and k not in p["spec"] and k != "url":
            d[k] = v
    d["extra"] = {"preset": preset_id, "preset_title": p["title"]}
    return DownloadSpec.from_dict(d)
