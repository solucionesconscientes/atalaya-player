"""H20 · «Convertir» and «Tareas» end to end (headless mpv + mpvd + real ffmpeg): the menu of the open file, options
(resolution, range from the A-B marks), the output folder text box, starting, live progress in «Tareas» (conversions
and downloads together), actions (open folder, remove), a whole folder, and the entries in «Descargas y conversión»
with ⌫ back to it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import APP, APP_FOLDER, start_mpv
from tests.test_nav import press, wait_nav

MU_OPTS = ("--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
           "mu-convert-speed=fast,mu-convert-open_command=true")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("mu-convert-src")
    srt = d / "s.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:02,500\nHola\n", encoding="utf-8")
    out = d / "clip.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4", "-i", str(srt),
                    "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast", "-g", "10",
                    "-c:a", "aac", "-c:s", "srt", str(out)], check=True)
    return out


@pytest.fixture
def conv_mpv(daemon_env):
    daemon_env.extra_env["MPV_UOS_VAAPI"] = "0"          # deterministic: CPU (VA-API has its own test)
    daemon_env.extra_env["MPV_UOS_YTDLP_AUTO_UPDATE"] = "0"
    (daemon_env.data_dir / "downloads.json").write_text(json.dumps([
        {"id": "d1", "url": "https://example.test/v", "title": "Vídeo descargado", "status": "done",
         "out_dir": str(daemon_env.base / "dl"), "outputs": [], "created_at": 1.0, "finished_at": 2.0,
         "spec": {"url": "https://example.test/v"}},
    ]))
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def ev(h, event: dict, message: str = "mu-convert-event") -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_convert", message, json.dumps({**base, **event}))


def conv(h, pred, timeout: float = 30.0) -> dict:
    return h.wait_property("user-data/mu/convert", lambda v: bool(v) and pred(v), timeout=timeout)


def titles(v: dict) -> list[str]:
    return [i["title"] for i in v.get("items") or []]


def item(v: dict, title: str) -> dict:
    return next(i for i in v["items"] if i["title"] == title)


def set_output(h, folder: Path) -> None:
    ev(h, {"type": "activate", "index": 1, "value": {"choose_dir": True}})
    conv(h, lambda v: v.get("input") == "dir")
    ev(h, {"type": "activate", "index": 1, "value": {"save": str(folder)}}, "mu-convert-input-event")
    conv(h, lambda v: v.get("dir") == str(folder) and v.get("input") == "")


def test_convert_open_file_with_range_and_tasks_panel(conv_mpv, clip, tmp_path):
    h, _d = conv_mpv
    out_dir = tmp_path / "Convertidos"
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    h.command("script-binding", "mu_convert/convert-menu")
    wait_nav(h, "mu-convert", f"{APP} › Convertir")
    st = conv(h, lambda v: v.get("view") == "root" and "MP4 compatible" in titles(v))
    t = titles(st)
    assert t[0] == "clip.mkv"   # (the «Atrás» row is added by mu/nav on the way to uosc)
    assert t[1:10] == ["MP4 compatible", "Más pequeño (H.265)", "Web (WebM)", "Solo audio · MP3", "Solo audio · M4A (AAC)",
                       "Solo audio · Opus", "Solo audio · FLAC", "Solo audio · WAV", "GIF animado"]
    assert {"Convertir una carpeta entera…", "Tareas", "Carpeta de salida", "Usar la tarjeta gráfica"} <= set(t)
    st = conv(h, lambda v: item(v, "Usar la tarjeta gráfica")["hint"] == "no disponible")   # MPV_UOS_VAAPI=0
    assert st["default_dir"].endswith(f"{APP_FOLDER}/Convertidos")
    set_output(h, out_dir)

    # A-B marks already set (the «l» loop): the range is on by default
    h.command("set", "ab-loop-a", "1")
    h.command("set", "ab-loop-b", "3")
    ev(h, {"type": "activate", "index": 3, "value": {"preset": "mp4", "source": str(clip)}})
    wait_nav(h, "mu-convert", f"{APP} › Convertir › MP4 compatible")
    st = conv(h, lambda v: v.get("view") == "options" and "Resolución máxima" in titles(v))
    assert st["preset"] == "mp4" and st["range"] is True and item(st, "Solo un tramo")["hint"] == "0:01–0:03"
    assert titles(st)[0] == "Convertir ahora" and item(st, "Resolución máxima")["hint"] == "original"
    assert item(st, "Conservar los subtítulos")["hint"] == "sí"
    for hint in ("1080p", "720p", "480p"):
        ev(h, {"type": "activate", "index": 3, "value": {"opt": "height"}})
        conv(h, lambda v, x=hint: item(v, "Resolución máxima")["hint"] == x)
    ev(h, {"type": "activate", "index": 2, "value": {"start": True}})
    wait_nav(h, "mu-convert", f"{APP} › Convertir › MP4 compatible › Tareas")
    st = conv(h, lambda v: v.get("last_done", {}).get("status") == "done", timeout=60)
    out = Path(st["last_done"]["file"])
    assert out == out_dir / "clip [00.00.01-00.00.03].mp4" and out.exists()
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,height", "-of",
                                      "json", str(out)], capture_output=True, text=True, check=True).stdout)
    assert [s["codec_name"] for s in info["streams"]] == ["h264", "aac", "mov_text"]
    assert info["streams"][0]["height"] == 480
    assert st["options"]["height"] == 480 and len(st["last_started"]) == 1

    # «Tareas»: the conversion (done) and the download from the history, each with its actions
    cid = st["last_started"][0]
    st = conv(h, lambda v: any(i["value"] == {"task": {"type": "convert", "id": cid}} and i["hint"] == "completado"
                               for i in v["items"]))
    row = next(i for i in st["items"] if i["value"] == {"task": {"type": "convert", "id": cid}})
    assert row["actions"] == ["retry", "remove", "folder"] and row["title"].startswith("clip.mkv  ·  Conversión: MP4")
    dl = next(i for i in st["items"] if i["value"] == {"task": {"type": "download", "id": "d1"}})
    assert dl["title"].startswith("Vídeo descargado  ·  Descarga")
    ev(h, {"type": "activate", "index": 2, "action": "folder", "value": {"task": {"type": "convert", "id": cid}}})
    conv(h, lambda v: v.get("opened") == str(out_dir))
    ev(h, {"type": "activate", "index": 2, "action": "remove", "value": {"task": {"type": "convert", "id": cid}}})
    conv(h, lambda v: all(i["value"] != {"task": {"type": "convert", "id": cid}} for i in v["items"])
         and v.get("view") == "tasks")
    assert out.exists()   # forgetting keeps the file

    # ⌫: tasks → options → Convertir → main menu
    press(h, "BS")
    wait_nav(h, "mu-convert", f"{APP} › Convertir › MP4 compatible")
    press(h, "BS")
    wait_nav(h, "mu-convert", f"{APP} › Convertir")
    press(h, "BS")
    wait_nav(h, "mu-menu", APP)
    press(h, "ESC")
    assert h.script_errors() == [], h.script_errors()


def test_folder_from_downloads_menu_and_tasks_entry(conv_mpv, clip, tmp_path):
    h, _d = conv_mpv
    folder = tmp_path / "Clases"
    folder.mkdir()
    for name in ("tema 1.mkv", "tema 2.mkv"):
        shutil.copyfile(clip, folder / name)
    out_dir = tmp_path / "out"

    # «Descargas y conversión» has the two new entries; they open mu-convert one level below it
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    wait_nav(h, "mu-ytdl", f"{APP} › Descargas y conversión")
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "root", timeout=10)
    assert {"Convertir…", "Tareas"} <= {i["title"] for i in st["items"]}
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-event", json.dumps(
        {"type": "activate", "index": 1, "menu_id": "{root}", "value": {"child": "convert-menu"}}))
    wait_nav(h, "mu-convert", f"{APP} › Descargas y conversión › Convertir")
    st = conv(h, lambda v: v.get("view") == "root" and "Tareas" in titles(v))
    assert "Abre un archivo de tu equipo para convertirlo" in titles(st)
    set_output(h, out_dir)

    # a whole folder: text box → presets of the folder → options → start (one task per file)
    ev(h, {"type": "activate", "index": 3, "value": {"folder_input": True}})
    conv(h, lambda v: v.get("input") == "folder")
    ev(h, {"type": "search", "query": str(folder)}, "mu-convert-input-event")
    ev(h, {"type": "activate", "index": 1, "value": {"save": str(folder)}}, "mu-convert-input-event")
    wait_nav(h, "mu-convert", f"{APP} › Descargas y conversión › Convertir › Carpeta Clases")
    st = conv(h, lambda v: v.get("view") == "folder" and "Solo audio · MP3" in titles(v))
    ev(h, {"type": "activate", "index": 5, "value": {"preset": "mp3", "source": str(folder), "folder": True}})
    st = conv(h, lambda v: v.get("view") == "options" and "Bitrate del audio" in titles(v))
    assert st["folder"] is True and titles(st)[0] == "Convertir toda la carpeta" and "Solo un tramo" not in titles(st)
    assert "Resolución máxima" not in titles(st)
    ev(h, {"type": "activate", "index": 3, "value": {"opt": "audio_bitrate"}})
    conv(h, lambda v: item(v, "Bitrate del audio")["hint"] == "128 kbps")
    ev(h, {"type": "activate", "index": 1, "value": {"start": True}})
    st = conv(h, lambda v: len(v.get("last_started") or []) == 2)
    ids = set(st["last_started"])
    st = conv(h, lambda v: sum(1 for t in v["tasks"] if t["id"] in ids and t["status"] == "done") == 2, timeout=60)
    assert sorted(p.name for p in (out_dir / "Clases").iterdir()) == ["tema 1.mp3", "tema 2.mp3"]
    assert st["tasks_active"] == 0

    # «Tareas» from the downloads menu: ⌫ goes back to it
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    wait_nav(h, "mu-ytdl", f"{APP} › Descargas y conversión")
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-event", json.dumps(
        {"type": "activate", "index": 1, "menu_id": "{root}", "value": {"child": "tasks-menu"}}))
    wait_nav(h, "mu-convert", f"{APP} › Descargas y conversión › Tareas")
    st = conv(h, lambda v: v.get("view") == "tasks" and "Limpiar terminadas" in titles(v))
    assert sum(1 for i in st["items"] if i["value"] and i["value"].get("task", {}).get("type") == "convert") == 2
    press(h, "BS")
    wait_nav(h, "mu-ytdl", f"{APP} › Descargas y conversión")
    press(h, "ESC")
    assert h.script_errors() == [], h.script_errors()
