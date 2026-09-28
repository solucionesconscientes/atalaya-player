"""Download specifications → exact yt-dlp argument lists.

Every option below was verified against ``yt-dlp --help`` of the vendored release (docs/YTDLP.md §5/§9).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CONTAINERS = ("mp4", "mkv", "webm")
AUDIO_FORMATS = ("mp3", "opus", "m4a", "flac", "wav")
AUDIO_BITRATES = (96, 128, 160, 192, 256, 320)
SPONSORBLOCK_MODES = ("none", "mark", "remove")
KINDS = ("video", "exact", "audio_original", "audio_convert")

# Container policy. Merging (video+audio) takes a preference list: yt-dlp picks the first container compatible with the
# codecs (get_compatible_ext) and falls back to mkv. Remuxing a single-file format fails when the codecs do not fit the
# target ("If the target container does not support the video/audio codec, remuxing will fail"), so only safe pairs
# are remuxed; mkv accepts anything.
MERGE_PREFERENCES = {"mp4": "mp4/mkv", "mkv": "mkv", "webm": "webm/mkv"}
REMUX_RULES = {"mp4": "mov>mp4/m4v>mp4/flv>mp4/3gp>mp4", "mkv": "mkv", "webm": ""}

PROGRESS_PREFIX = "MU_PROGRESS"
POSTPROCESS_PREFIX = "MU_PP"
DONE_PREFIX = "MU_DONE"
DEFAULT_TEMPLATE = "%(title).120B [%(id)s].%(ext)s"
DEFAULT_SUB_LANGS = "es.*,en.*"


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
    chapters: bool = False
    thumbnail: bool = False
    metadata: bool = False
    sponsorblock: str = "none"          # none | mark | remove
    playlist: bool = False              # whole playlist vs only the referenced item
    playlist_items: str | None = None   # e.g. "1:5"
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

    @property
    def is_audio(self) -> bool:
        return self.kind in ("audio_original", "audio_convert")

    def format_expression(self) -> str:
        if self.kind == "exact":
            return str(self.format)
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


def output_args(out_dir: str, template: str = DEFAULT_TEMPLATE) -> list[str]:
    return ["-P", out_dir, "-o", template]


def progress_args() -> list[str]:
    return [
        "--newline", "--progress-delta", "0.2",
        "--progress-template", f"download:{PROGRESS_PREFIX} %(progress)j",
        "--progress-template", f"postprocess:{POSTPROCESS_PREFIX} %(progress)j",
        "--print", f"after_move:{DONE_PREFIX} %(.{{id,title,ext,filepath,format_id,duration}})j",
        "--no-simulate",
    ]


def build_args(spec: DownloadSpec, out_dir: str, template: str = DEFAULT_TEMPLATE) -> list[str]:
    """Arguments after the binary (+ its base args); the URL is last."""
    spec.validate()
    args: list[str] = ["--no-overwrites", "--continue", "--ignore-errors", "--retries", "5", "--socket-timeout", "30"]
    args += ["-f", spec.format_expression()]
    if spec.is_audio:
        args += ["-x"]
        if spec.kind == "audio_convert":
            args += ["--audio-format", spec.audio_format]
            if spec.audio_format not in ("flac", "wav"):
                if spec.audio_bitrate is not None:
                    args += ["--audio-quality", f"{int(spec.audio_bitrate)}K"]
                else:
                    args += ["--audio-quality", str(int(spec.audio_vbr))]
        # audio_original: -x without --audio-format keeps the original codec (FFmpegExtractAudio "best" = stream copy)
    else:
        args += ["--merge-output-format", MERGE_PREFERENCES[spec.container]]
        if REMUX_RULES[spec.container]:
            args += ["--remux-video", REMUX_RULES[spec.container]]
    if spec.subtitles and not spec.is_audio:
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", spec.sub_langs, "--embed-subs"]
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
    else:
        args += ["--no-playlist"]
    args += output_args(out_dir, template)
    args += progress_args()
    args += ["--", spec.url]
    return args


# -- presets shown in the "Descargar" menu ------------------------------------------------------------

PRESETS: list[dict[str, Any]] = [
    {"id": "video_best", "title": "Vídeo · mejor calidad", "group": "video", "spec": {"kind": "video", "height": None}},
    {"id": "video_1080", "title": "Vídeo · 1080p", "group": "video", "spec": {"kind": "video", "height": 1080}},
    {"id": "video_720", "title": "Vídeo · 720p", "group": "video", "spec": {"kind": "video", "height": 720}},
    {"id": "video_480", "title": "Vídeo · 480p", "group": "video", "spec": {"kind": "video", "height": 480}},
    {"id": "video_360", "title": "Vídeo · 360p", "group": "video", "spec": {"kind": "video", "height": 360}},
    {"id": "audio_original", "title": "Audio · original (sin recodificar)", "group": "audio",
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
    {"id": "audio_wav", "title": "Audio · WAV", "group": "audio",
     "spec": {"kind": "audio_convert", "audio_format": "wav", "audio_bitrate": None}},
]


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
