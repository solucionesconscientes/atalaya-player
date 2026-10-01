"""H18 · the ● «Grabar» button end to end (headless mpv + mpvd): «grabar desde ahora» on a local file (lossless range,
video and audio only), «recortar un tramo» with the A-B marks, a live stream with stream-record (and audio only kept
afterwards), the red dot + counter while recording and ⌫ back to the main menu.
H43: la fila «Formato» (igual que el original / MP4 / solo audio) manda el envase y se recuerda."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import start_mpv
from tests.test_mu_iptv import free_port, start_live_stream
from tests.test_mu_ytdl import ytdl_mpv  # noqa: F401 - fixture: fake yt-dlp wired into mpv and mpvd
from tests.test_nav import press, wait_nav

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


@pytest.fixture
def rec_mpv(daemon_env, media_dir, tmp_path):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def ev(h, message: str, event: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_record", message, json.dumps({**base, **event}))


def rec_state(h, pred, timeout: float = 30.0):
    return h.wait_property("user-data/mu/record", lambda v: bool(v) and pred(v), timeout=timeout)


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def kinds(info: dict) -> list[str]:
    return sorted(s["codec_type"] for s in info["streams"])


def seek_to(h, t: float) -> None:
    h.command("seek", t, "absolute+exact")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - t) < 0.3, timeout=10)
    h.wait_property("seeking", lambda v: v is False, timeout=10)
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - t) < 0.3, timeout=10)


def set_folder(h, folder: Path) -> None:
    ev(h, "mu-record-event", {"type": "activate", "index": 1, "value": {"choose_dir": True}})
    rec_state(h, lambda v: v.get("input") == "dir")
    ev(h, "mu-record-input-event", {"type": "activate", "index": 1, "value": {"save": str(folder)}})
    rec_state(h, lambda v: v.get("dir") == str(folder) and v.get("input") == "")


def test_record_local_ranges_cut_and_menu(rec_mpv, media_dir, tmp_path):
    h, _d = rec_mpv
    out_dir = tmp_path / "Grabaciones"
    # a copy with a keyframe every second (the fixture has one every 10 s): a lossless cut starts on the previous one
    video = tmp_path / "video30.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media_dir / "video30.mkv"), "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "25", "-c:a", "copy", str(video)], check=True)
    h.command("loadfile", str(video))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    h.command("script-binding", "mu_record/record-menu")
    wait_nav(h, "mu-record", "MPV-UOS › Grabar")
    st = rec_state(h, lambda v: v.get("view") == "root" and v.get("items"))
    titles = [i["title"] for i in st["items"]]
    assert titles[:2] == ["Captura de pantalla", "Captura sin subtítulos"] and "Grabar desde ahora" in titles
    # H43/B1: una sola fila para grabar (antes había otra para «solo el audio») y el envase en su propia fila
    assert "Grabar solo el audio desde ahora" not in titles
    assert next(i for i in st["items"] if i["title"] == "Grabar desde ahora")["hint"] == "sin recodificar · MKV"
    assert next(i for i in st["items"] if i["title"] == "Formato")["hint"] == "Igual que el original (MKV)"
    assert st["format"] == "copy"
    set_folder(h, out_dir)

    # «grabar desde ahora» from 5 s, stop at 11 s → lossless cut of 6 s with video and audio, en MKV (el valor por
    # defecto: el que nunca falla). Antes se elegía MP4 por detrás cuando los códecs cabían, sin decirlo.
    seek_to(h, 5.0)
    ev(h, "mu-record-event", {"type": "activate", "index": 3, "value": {"start": True}})
    st = rec_state(h, lambda v: v.get("recording") is True and v.get("mode") == "range")
    assert abs(st["start"] - 5.0) < 0.3
    seek_to(h, 11.0)
    h.command("script-binding", "mu_record/record-toggle")
    st = rec_state(h, lambda v: v.get("recording") is False and v.get("last", {}).get("status") == "done", timeout=60)
    f = st["last"]["file"]
    assert Path(f).parent == out_dir and f.endswith(".mkv") and "video30" in Path(f).name
    info = probe(f)
    # en MKV el corte arranca en el fotograma clave anterior a la marca (aquí hay uno por segundo), así que el tramo
    # pedido de 6 s sale igual o algo más largo; con MP4 empieza exactamente en la marca (lista de edición)
    assert kinds(info) == ["audio", "video"] and 6.0 - 0.3 <= float(info["format"]["duration"]) <= 7.2

    # H43/B1: se elige MP4 (estos códecs sí caben: H.264 + AAC) y se recuerda; el siguiente tramo sale en .mp4
    h.command("script-binding", "mu_record/record-menu")
    rec_state(h, lambda v: v.get("view") == "root")
    ev(h, "mu-record-event", {"type": "activate", "index": 4, "value": {"view": "format"}})
    st = rec_state(h, lambda v: v.get("view") == "format")
    fila = next(i for i in st["items"] if i["title"] == "MP4 si los códecs lo permiten")
    assert "caben" in fila["hint"] and "NO caben" not in fila["hint"]
    ev(h, "mu-record-event", {"type": "activate", "index": 2, "value": {"format": "mp4"}})
    st = rec_state(h, lambda v: v.get("format") == "mp4" and v.get("view") == "root")
    assert next(i for i in st["items"] if i["title"] == "Grabar desde ahora")["hint"] == "sin recodificar · MP4"
    seek_to(h, 5.0)
    ev(h, "mu-record-event", {"type": "activate", "index": 3, "value": {"start": True}})
    rec_state(h, lambda v: v.get("recording") is True)
    seek_to(h, 11.0)
    h.command("script-binding", "mu_record/record-toggle")
    st = rec_state(h, lambda v: v.get("recording") is False and v.get("last", {}).get("file", "").endswith(".mp4"),
                   timeout=60)
    info = probe(st["last"]["file"])
    assert kinds(info) == ["audio", "video"] and abs(float(info["format"]["duration"]) - 6.0) < 0.3
    h.command("script-binding", "mu_record/record-menu")
    rec_state(h, lambda v: v.get("view") == "root")
    ev(h, "mu-record-event", {"type": "activate", "index": 4, "value": {"view": "format"}})
    rec_state(h, lambda v: v.get("view") == "format")
    ev(h, "mu-record-event", {"type": "activate", "index": 1, "value": {"format": "copy"}})
    rec_state(h, lambda v: v.get("format") == "copy" and v.get("view") == "root")

    # audio only (la tecla de siempre), 2 → 8 s: the original AAC in .m4a
    seek_to(h, 2.0)
    h.command("script-message-to", "mu_record", "mu-record-start", "audio")
    rec_state(h, lambda v: v.get("recording") is True and v.get("audio") is True)
    seek_to(h, 8.0)
    h.command("script-message-to", "mu_record", "mu-record-stop")
    st = rec_state(h, lambda v: v.get("last", {}).get("file", "").endswith(".m4a"), timeout=60)
    info = probe(st["last"]["file"])
    assert kinds(info) == ["audio"] and info["streams"][0]["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.3

    # «recortar un tramo»: marks from the menu (they are mpv's A-B loop points), then save
    h.command("script-binding", "mu_record/record-menu")
    rec_state(h, lambda v: v.get("view") == "root")
    ev(h, "mu-record-event", {"type": "activate", "index": 5, "value": {"view": "cut"}})
    wait_nav(h, "mu-record", "MPV-UOS › Grabar › Recortar un tramo")
    seek_to(h, 20.0)
    ev(h, "mu-record-event", {"type": "activate", "index": 1, "value": {"mark": "a"}})
    seek_to(h, 24.0)
    ev(h, "mu-record-event", {"type": "activate", "index": 2, "value": {"mark": "b"}})
    h.wait_property("ab-loop-b", lambda v: isinstance(v, (int, float)) and abs(v - 24.0) < 0.3, timeout=10)
    assert abs(h.get("ab-loop-a") - 20.0) < 0.3
    ev(h, "mu-record-event", {"type": "activate", "index": 3, "value": {"save": True}})
    st = rec_state(h, lambda v: "00.00.20-00.00.24" in v.get("last", {}).get("file", ""), timeout=60)
    assert kinds(probe(st["last"]["file"])) == ["audio", "video"]

    # ⌫ from the root of Grabar → main menu
    h.command("script-binding", "mu_record/record-menu")
    wait_nav(h, "mu-record", "MPV-UOS › Grabar")
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "ESC")
    assert h.script_errors() == [], h.script_errors()


def test_record_live_stream_with_counter_and_audio_only(rec_mpv, tmp_path):
    h, _d = rec_mpv
    port = free_port()
    live = start_live_stream(port)
    try:
        out_dir = tmp_path / "Directos"
        h.command("script-binding", "mu_record/record-menu")
        rec_state(h, lambda v: v.get("view") == "root")
        set_folder(h, out_dir)
        press(h, "ESC")
        h.command("set_property", "pause", False)
        url = f"http://127.0.0.1:{port}/live.ts"
        time.sleep(1.0)  # ffmpeg -listen needs a moment before it accepts the connection
        for _ in range(10):
            h.command("loadfile", url)
            try:
                h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.5, timeout=8)
                break
            except TimeoutError:
                continue

        h.command("script-binding", "mu_record/record-toggle")
        st = rec_state(h, lambda v: v.get("recording") is True and v.get("mode") == "live")
        assert st["stream_record"].startswith(str(out_dir)) and st["stream_record"].endswith(".mkv")
        rec_state(h, lambda v: v.get("indicator", "").startswith("REC 0:0") and v["indicator"] != "REC 0:00", timeout=10)
        time.sleep(3.0)
        h.command("script-binding", "mu_record/record-toggle")
        st = rec_state(h, lambda v: v.get("recording") is False and v.get("last", {}).get("status") == "done")
        f = st["last"]["file"]
        info = probe(f)
        assert kinds(info) == ["audio", "video"] and float(info["format"]["duration"]) > 1.5

        # audio only: mpv records the stream, mpvd keeps the AAC track (stream copy) and removes the video file
        h.command("script-message-to", "mu_record", "mu-record-start", "audio")
        st = rec_state(h, lambda v: v.get("recording") is True and v.get("audio") is True)
        video_file = st["stream_record"]
        time.sleep(3.0)
        h.command("script-message-to", "mu_record", "mu-record-stop")
        st = rec_state(h, lambda v: v.get("last", {}).get("file", "").endswith(".m4a"), timeout=60)
        assert kinds(probe(st["last"]["file"])) == ["audio"] and not Path(video_file).exists()
        assert h.script_errors() == [], h.script_errors()
    finally:
        live.kill()
        live.wait()


def test_record_internet_video_range_with_yt_dlp_sections(ytdl_mpv):
    """An internet video: «grabar desde ahora» 3 → 9 s becomes a yt-dlp download of that section, in the recordings
    folder, and the result is announced when the download finishes."""
    h, _d, arglog, tmp = ytdl_mpv
    from tests.test_mu_ytdl import URL, argv_lines

    out_dir = tmp / "Grabaciones"
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    h.command("loadfile", URL)
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=40)
    h.command("script-binding", "mu_record/record-menu")
    st = rec_state(h, lambda v: v.get("view") == "root" and v.get("items"))
    assert next(i for i in st["items"] if i["title"] == "Grabar desde ahora")["hint"] == \
        "tramo del vídeo de internet · MKV"
    set_folder(h, out_dir)
    seek_to(h, 3.0)
    h.command("script-binding", "mu_record/record-toggle")
    rec_state(h, lambda v: v.get("recording") is True and v.get("mode") == "range")
    seek_to(h, 9.0)
    h.command("script-binding", "mu_record/record-toggle")
    st = rec_state(h, lambda v: v.get("last", {}).get("status") == "done", timeout=60)
    assert Path(st["last"]["file"]).parent == out_dir
    dl = next(a for a in argv_lines(arglog) if "--download-sections" in a)
    a, b = dl[dl.index("--download-sections") + 1].lstrip("*").split("-")
    assert abs(float(a) - 3.0) < 0.3 and abs(float(b) - 9.0) < 0.3 and dl[-1] == URL
    assert dl[dl.index("-P") + 1] == str(out_dir) and "%(section_start)d-%(section_end)d" in dl[dl.index("-o") + 1]
    assert h.script_errors() == [], h.script_errors()
