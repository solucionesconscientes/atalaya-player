"""H25 · «Emitir en directo» (RTMP/RTMPS with ffmpeg): server and key validation, the 0600 settings file, redaction
of the key, the ffmpeg arguments (CPU / VA-API, -re and the host's position, live inputs, black picture or silence
when a track is missing, 720p/30 fps caps), and real emissions to a local RTMP receiver (``ffmpeg -listen 1``):
with the GPU attempt failing and the CPU retry, stopping (only its own PID), errors without the key, and the whole
flow from mu-share's menu with mpvd and a headless mpv (key pasted from a test clipboard, never published)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import time
from pathlib import Path

import pytest

from mpvd.convert.presets import HwPlan
from mpvd.rpc import RpcError
from mpvd.share import hls, live
from tests.conftest import start_mpv
from tests.test_mu_share import ev, share, titles
from tests.test_nav import wait_nav
from tests.test_share_http import MU_OPTS

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="sin ffmpeg")

KEY = "abcd-EFGH-1234-ijkl-5678"


def _probe_json(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("live-src")
    short = d / "corto.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4", "-c:v", "libx264",
                    "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-metadata", "title=Corto", str(short)],
                   check=True)
    long = d / "largo.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=40", "-c:v", "libx264",
                    "-preset", "ultrafast", "-g", "50", "-c:a", "aac", str(long)], check=True)
    return {"short": short, "long": long}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Receiver:
    """A local RTMP server: ``ffmpeg -listen 1`` storing what it gets (killed by its own PID if still alive).
    Built by ``_receiver``: it is never poked with a test connection (-listen 1 accepts one client only)."""

    port: int
    out: Path
    log: Path
    url: str
    proc: subprocess.Popen[bytes]

    def wait(self, timeout: float = 60) -> int:
        return self.proc.wait(timeout=timeout)

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._log_fh.close()


def _receiver(out: Path, key: str = KEY) -> Receiver:
    """Start the receiver without poking it (a probe connection would be its only accepted client)."""
    r = Receiver()
    r.port = _free_port()
    r.out = out
    r.log = out.with_suffix(".log")
    r.url = f"rtmp://127.0.0.1:{r.port}/live"
    r._log_fh = open(r.log, "wb")  # noqa: SIM115 - closed in stop()
    r.proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-listen", "1", "-timeout", "60",
         "-f", "flv", "-i", f"{r.url}/{key}", "-c", "copy", "-y", str(out)],
        stdout=subprocess.DEVNULL, stderr=r._log_fh)
    # wait until the port is bound (read /proc/net/tcp instead of connecting: -listen 1 takes one client only)
    hexport = f"{r.port:04X}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            rows = Path("/proc/net/tcp").read_text().splitlines()[1:]
        except OSError:
            time.sleep(0.5)
            break
        if any(row.split()[1].endswith(":" + hexport) and row.split()[3] == "0A" for row in rows):
            break
        time.sleep(0.05)
    return r


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is not running
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


# -- settings, validation, redaction ---------------------------------------------------------------------------------


def test_server_and_key_validation():
    assert live.validate_server(" rtmp://a.rtmp.youtube.com/live2/ ") == "rtmp://a.rtmp.youtube.com/live2"
    assert live.validate_server("rtmps://a.rtmps.youtube.com:443/live2") == "rtmps://a.rtmps.youtube.com:443/live2"
    assert live.validate_server("rtmp://peertube.example:1935/live") == "rtmp://peertube.example:1935/live"
    for bad in ("", "http://x/live", "rtmp://host", "rtmp://host/", "rtmp://u:p@host/live", "rtmp://host/live?k=1",
                "rtmp://host/li ve", "rtmp://host:99999/live", "rtmp:///live", None):
        with pytest.raises(ValueError):
            live.validate_server(bad)
    assert live.validate_key(f" {KEY} ") == KEY
    for bad in ("", "abc", "con espacio", "x" * 513, "ñandú-1234", "tab\there"):
        with pytest.raises(ValueError):
            live.validate_key(bad)
    assert live.server_host("rtmp://live.twitch.tv/app") == "live.twitch.tv"
    assert live.service_of("rtmps://a.rtmps.youtube.com:443/live2") == "YouTube"
    assert live.service_of("rtmp://live.twitch.tv/app") == "Twitch"
    assert live.target_url("rtmp://h/live/", KEY) == f"rtmp://h/live/{KEY}"
    assert set(live.PRESETS) == {"youtube", "youtube-rtmps", "twitch"}
    for _, url in live.PRESETS.values():
        live.validate_server(url)


def test_redact():
    key = "live_12/34?x=y&z"
    url = live.target_url("rtmp://h:1935/app", key)
    text = f"Error opening output {url}: Connection refused; quoted {key.replace('/', '%2F').replace('?', '%3F')}"
    text = text.replace("%3Fx=y&z", "%3Fx%3Dy%26z")
    out = live.redact(text, [key, url])
    assert key not in out and "12%2F34" not in out and out.count(live.MASK) == 2, out
    assert live.redact("nada que ocultar", [key]) == "nada que ocultar"
    assert live.redact("abc", ["", "ab"]) == "abc"   # too short to be a key: nothing replaced


def test_settings_file_is_private(tmp_path):
    s = live.LiveSettings(tmp_path)
    assert s.public() == {"configured": False, "server_host": "", "has_server": False, "has_key": False,
                          "key_length": 0, "service": ""}
    s.save(server="rtmp://a.rtmp.youtube.com/live2")
    s.save(key=KEY)
    path = tmp_path / "live.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"server": "rtmp://a.rtmp.youtube.com/live2", "key": KEY}
    pub = s.public()
    assert pub["configured"] and pub["service"] == "YouTube" and pub["key_length"] == len(KEY)
    assert KEY not in json.dumps(pub)
    with pytest.raises(ValueError):
        s.save(key="con espacios no")
    assert s.key == KEY                                   # an invalid value changes nothing
    os.chmod(path, 0o644)
    again = live.LiveSettings(tmp_path)                   # loading tightens loose permissions
    assert again.key == KEY and stat.S_IMODE(path.stat().st_mode) == 0o600
    again.save(key="")
    assert not again.configured and live.LiveSettings(tmp_path).key == ""


# -- the ffmpeg command ----------------------------------------------------------------------------------------------


def _probe(video: dict | None = None, audio: list[dict] | None = None, duration: float | None = 60.0) -> dict:
    streams = []
    if video is not None:
        streams.append({"index": 0, "codec_type": "video", "codec_name": "h264", **video})
    for i, a in enumerate(audio or []):
        streams.append({"index": len(streams), "codec_type": "audio", "codec_name": "aac", **a})
    return {"streams": streams, "format": {"duration": str(duration)} if duration else {}}


def _arg(cmd: list[str], opt: str) -> str:
    return cmd[cmd.index(opt) + 1]


def test_command_cpu_file_from_position():
    target = live.target_url("rtmp://h/live2", KEY)
    p = _probe({"height": 1080, "width": 1920, "avg_frame_rate": "60/1"}, [{}, {}])
    plan = live.build_command([hls.Input("/v/peli.mkv")], [p], target, start=125.5, audio_index=2, ffmpeg="ffmpeg")
    cmd = plan.cmd
    assert plan.video == "cpu" and cmd[0] == "ffmpeg" and cmd[-1] == target
    assert cmd.index("-re") < cmd.index("-ss") < cmd.index("-i") and _arg(cmd, "-ss") == "125.500"
    assert _arg(cmd, "-progress") == "pipe:1" and "-nostats" in cmd and _arg(cmd, "-loglevel") == "error"
    assert _arg(cmd, "-vf") == "fps=30,scale=-2:720,format=yuv420p"      # 720p and 30 fps at most
    assert _arg(cmd, "-c:v") == "libx264" and _arg(cmd, "-preset") == "veryfast"
    assert _arg(cmd, "-b:v") == "2500k" and _arg(cmd, "-maxrate") == "2500k" and _arg(cmd, "-bufsize") == "5000k"
    assert _arg(cmd, "-g") == "60" and _arg(cmd, "-keyint_min") == "60"    # a keyframe every 2 s
    assert _arg(cmd, "-c:a") == "aac" and _arg(cmd, "-b:a") == "128k" and _arg(cmd, "-ar") == "44100"
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["0:0", "0:2"]                                           # the audio the host listens to
    assert _arg(cmd, "-f") == "flv" and _arg(cmd, "-flvflags") == "no_duration_filesize"
    assert "-shortest" not in cmd and "-vaapi_device" not in cmd


def test_command_small_video_and_live_input():
    target = "rtmp://h/app/k1234"
    p = _probe({"height": 240, "avg_frame_rate": "25/1"}, [{}], duration=None)
    cmd = live.build_command([hls.Input("https://tv.example/canal.m3u8", {"User-Agent": "UA"})], [p], target,
                             start=300, live=True).cmd
    assert "-re" not in cmd and "-ss" not in cmd                            # a live input goes as it comes
    assert _arg(cmd, "-vf") == "format=yuv420p" and _arg(cmd, "-g") == "50"
    assert _arg(cmd, "-user_agent") == "UA" and "-reconnect" in cmd


def test_command_missing_tracks_get_black_or_silence():
    target = "rtmp://h/app/k1234"
    radio = live.build_command([hls.Input("/r.mp3")], [_probe(None, [{}])], target).cmd
    assert live.BLACK in radio and "-shortest" in radio
    maps = [radio[i + 1] for i, a in enumerate(radio) if a == "-map"]
    assert maps == ["1:0", "0:0"]
    mute = live.build_command([hls.Input("/m.mkv")], [_probe({"height": 480}, [])], target).cmd
    assert live.SILENCE in mute and "-shortest" in mute
    assert [mute[i + 1] for i, a in enumerate(mute) if a == "-map"] == ["0:0", "1:0"]
    with pytest.raises(ValueError):
        live.build_command([hls.Input("/x")], [{"streams": []}], target)
    # two inputs (web video + audio): -re/-ss before each
    two = live.build_command([hls.Input("https://v"), hls.Input("https://a")],
                             [_probe({"height": 720}, []), _probe(None, [{}])], target, start=10).cmd
    assert two.count("-re") == 2 and two.count("-ss") == 2
    assert [two[i + 1] for i, a in enumerate(two) if a == "-map"] == ["0:0", "1:0"]


def test_command_vaapi_and_cpu_retry():
    target = "rtmp://h/app/k1234"
    hw = HwPlan(device="/dev/dri/renderD128", codec="h264", low_power=True)
    plans = live.plans_for([hls.Input("/v.mkv")], [_probe({"height": 1080}, [{}])], target, hw=hw)
    assert [p.video for p in plans] == ["vaapi", "cpu"]
    cmd = plans[0].cmd
    assert cmd.index("-vaapi_device") < cmd.index("-i") and _arg(cmd, "-vaapi_device") == "/dev/dri/renderD128"
    assert _arg(cmd, "-vf") == "scale=-2:720,format=nv12,hwupload" and _arg(cmd, "-c:v") == "h264_vaapi"
    assert _arg(cmd, "-low_power") == "1" and _arg(cmd, "-rc_mode") == "CBR" and _arg(cmd, "-b:v") == "2500k"
    assert "-vaapi_device" not in plans[1].cmd and _arg(plans[1].cmd, "-c:v") == "libx264"
    assert len(live.plans_for([hls.Input("/v.mkv")], [_probe({"height": 480}, [{}])], target)) == 1
    assert live.friendly_error("[tcp @ 0x1] Connection refused") == "el servidor de emisión rechaza la conexión"


# -- real emissions to a local receiver -----------------------------------------------------------------------------------


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_real_emission_gpu_fails_then_cpu(clips, tmp_path):
    """The GPU attempt fails (no such device) before sending anything: the CPU retry reaches the receiver."""
    rx = _receiver(tmp_path / "out.flv")
    try:
        target = live.target_url(rx.url, KEY)
        src = hls.Input(str(clips["short"]))
        probes = [hls.probe(src)]
        hw = HwPlan(device=str(tmp_path / "no-render-node"), codec="h264", low_power=True)
        plans = live.plans_for([src], probes, target, hw=hw)
        changes: list[str] = []

        async def go() -> live.LiveRun:
            run = live.LiveRun(plans, [KEY, target], title="Corto", source="local",
                               on_change=lambda r: changes.append(r.status))
            run.start()
            deadline = time.monotonic() + 60
            while run.active and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            await run.stop()
            return run

        run = _run(go())
        assert run.status == "done", (run.info(), rx.text())
        assert run.mode == "cpu" and run.warnings == ["la tarjeta gráfica falló: se emite con la CPU"]
        assert changes == ["live", "done"]
        info = run.info()
        assert info["seconds"] > 3 and info["bytes"] > 0 and info["pid"] is None
        assert rx.wait(20) == 0
        assert "Unexpected stream" not in rx.text()          # the receiver got exactly our key
        data = _probe_json(rx.out)
        codecs = sorted(s["codec_name"] for s in data["streams"])
        assert codecs == ["aac", "h264"]
        a = next(s for s in data["streams"] if s["codec_type"] == "audio")
        assert a["sample_rate"] == "44100" and a["channels"] == 2
        assert 3.0 < float(data["format"]["duration"]) < 5.5
    finally:
        rx.stop()


def test_real_emission_stop_kills_only_its_pid(clips, tmp_path):
    rx = _receiver(tmp_path / "out.flv")
    try:
        target = live.target_url(rx.url, KEY)
        src = hls.Input(str(clips["long"]))
        plans = live.plans_for([src], [hls.probe(src)], target)

        async def go() -> tuple[live.LiveRun, int]:
            run = live.LiveRun(plans, [KEY, target])
            run.start()
            deadline = time.monotonic() + 30
            while run.status != "live" and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert run.status == "live", run.info()
            pid = run.info()["pid"]
            assert pid and _pid_alive(pid)
            await asyncio.sleep(1.5)
            await run.stop()
            return run, pid

        run, pid = _run(go())
        assert run.status == "stopped" and not _pid_alive(pid)
        assert rx.wait(20) is not None                       # the receiver ends when the sender goes away
        assert float(_probe_json(rx.out)["format"]["duration"]) > 1.0
    finally:
        rx.stop()


def test_emission_error_never_shows_the_key(clips):
    port = _free_port()   # nobody listens there
    target = live.target_url(f"rtmp://127.0.0.1:{port}/live", KEY)
    src = hls.Input(str(clips["short"]))
    plans = live.plans_for([src], [hls.probe(src)], target)

    async def go() -> live.LiveRun:
        run = live.LiveRun(plans, [KEY, target])
        run.start()
        await asyncio.wait_for(run.task, 30)
        return run

    run = _run(go())
    assert run.status == "failed" and run.error == "el servidor de emisión rechaza la conexión", run.info()
    blob = json.dumps(run.info()) + "\n".join(run.stderr)
    assert KEY not in blob and live.MASK in blob


# -- the whole flow: mpvd + headless mpv + mu-share ----------------------------------------------------------------------


@pytest.fixture
def live_env(daemon_env, clips):
    daemon_env.extra_env.update({"MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PORT": "0", "MPV_UOS_VAAPI": "0",
                                 "MPV_UOS_YTDLP_AUTO_UPDATE": "0", "MPVD_LIVE_CLIPBOARD_PROP": "user-data/test/clip"})
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes", str(clips["long"])],
                  env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
        yield h, daemon_env
    finally:
        h.stop()


def test_menu_configure_start_and_stop(live_env, clips, tmp_path):
    h, d = live_env
    assert d.call("capabilities")["services"]["live"] is True
    st = d.call("live.status")
    assert st["configured"] is False and st["active"] is False and "derecho a compartir" in st["legal"]
    with pytest.raises(RpcError, match="configura antes"):
        d.call("live.start")

    h.command("script-binding", "mu_share/share-menu")
    v = share(h, lambda v: "Emitir en directo…" in titles(v))
    ev(h, {"type": "activate", "index": 9, "value": {"view": "live"}})
    wait_nav(h, "mu-share", "MPV-UOS › Compartir › Emitir en directo")
    v = share(h, lambda v: v["view"] == "live" and "Emite solo lo que tengas derecho a compartir" in titles(v))
    assert "Emitir lo que estoy viendo" not in titles(v) and "Pegar la clave de emisión" in titles(v)
    # server: the presets, then another one typed in the text box
    ev(h, {"type": "activate", "index": 4, "value": {"view": "live_server"}})
    v = share(h, lambda v: v["view"] == "live_server" and "YouTube" in titles(v))
    assert {"YouTube (cifrado)", "Twitch", "Otro servidor (PeerTube, Owncast…)"} <= set(titles(v))
    ev(h, {"type": "activate", "index": 1, "value": {"live": "preset", "preset": "youtube"}})
    share(h, lambda v: v["live"]["server_host"] == "a.rtmp.youtube.com")
    rx = _receiver(tmp_path / "out.flv")
    try:
        h.command("script-binding", "mu_share/share-menu")
        ev(h, {"type": "activate", "index": 9, "value": {"view": "live"}})
        ev(h, {"type": "activate", "index": 4, "value": {"view": "live_server"}})
        share(h, lambda v: v["view"] == "live_server")
        ev(h, {"type": "activate", "index": 4, "value": {"action": "server-write"}})
        share(h, lambda v: v["input"] == "server")
        ev(h, {"type": "activate", "index": 1, "value": {"save": "http://nope"}}, "mu-share-input-event")
        share(h, lambda v: "rtmp://" in v["last_error"])
        ev(h, {"type": "activate", "index": 4, "value": {"action": "server-write"}})
        share(h, lambda v: v["input"] == "server")
        ev(h, {"type": "activate", "index": 1, "value": {"save": rx.url}}, "mu-share-input-event")
        v = share(h, lambda v: v["live"]["server_host"] == "127.0.0.1" and v["view"] == "live")
        # the key: from the (test) clipboard, read by mpvd; never published nor returned
        ev(h, {"type": "activate", "index": 5, "value": {"live": "key"}})
        share(h, lambda v: "portapapeles" in v["last_error"])
        h.command("set", "user-data/test/clip", KEY)
        ev(h, {"type": "activate", "index": 5, "value": {"live": "key"}})
        v = share(h, lambda v: v["live"]["has_key"] and v["live"]["configured"])
        assert v["live"]["key_length"] == len(KEY)
        v = share(h, lambda v: "Emitir lo que estoy viendo" in titles(v))
        path = d.data_dir / "live.json"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and json.loads(path.read_text())["key"] == KEY

        # start from the host's position (10 s into the file)
        h.command("seek", "10", "absolute")
        ev(h, {"type": "activate", "index": 3, "value": {"live": "start"}})
        v = share(h, lambda v: v["live"]["status"] == "live", timeout=40)
        assert v["live"]["mode"] == "cpu" and v["live"]["active"] is True
        v = share(h, lambda v: "Parar la emisión" in titles(v) and any(t.startswith("En directo") for t in titles(v)),
                  timeout=10)
        st = d.call("live.status")
        pid = st["run"]["pid"]
        assert st["active"] and _pid_alive(pid) and st["run"]["source"] == "local"
        with pytest.raises(RpcError, match="ya hay una emisión"):
            d.call("live.start")
        time.sleep(2)
        ev(h, {"type": "activate", "index": 4, "value": {"live": "stop"}})
        v = share(h, lambda v: v["live"]["status"] == "stopped" and not v["live"]["active"])
        assert not _pid_alive(pid)
        assert rx.wait(20) is not None and "Unexpected stream" not in rx.text()
        data = _probe_json(rx.out)
        assert sorted(s["codec_name"] for s in data["streams"]) == ["aac", "h264"]
        assert float(data["format"]["duration"]) > 1.0

        # the key never left mpvd: not in the menu state, the API, mpvd's log nor mpv's log
        state_blob = json.dumps(h.get("user-data/mu/share"), ensure_ascii=False)
        api_blob = json.dumps(d.call("live.status"), ensure_ascii=False)
        assert KEY not in state_blob and KEY not in api_blob
        mpvd_log = (d.cache_dir / "mpvd.log").read_text(encoding="utf-8", errors="replace")
        assert "live: sending local" in mpvd_log and KEY not in mpvd_log
        # (mpv's verbose log may show the test's own `set user-data/test/clip`: that line stands for the clipboard)
        mpv_log = [ln for ln in h.log_text().splitlines() if KEY in ln and "user-data/test/clip" not in ln]
        assert mpv_log == [], mpv_log[:3]

        # forget the key: off again
        ev(h, {"type": "activate", "index": 7, "value": {"live": "forget"}})
        share(h, lambda v: not v["live"]["has_key"] and not v["live"]["configured"])
        h.command("script-message-to", "uosc", "close-menu", "mu-share")
    finally:
        rx.stop()
    assert not h.script_errors(), h.script_errors()


def test_player_closing_stops_the_emission(live_env, tmp_path):
    h, d = live_env
    rx = _receiver(tmp_path / "out.flv")
    try:
        d.call("live.configure", {"server": rx.url, "key": KEY})
        st = d.call("live.start", {"from_start": True})
        assert st["active"] and st["run"]["status"] in ("connecting", "live")
        d.wait(lambda: d.call("live.status")["run"]["status"] == "live", timeout=40)
        pid = d.call("live.status")["run"]["pid"]
        h.command("quit")
        d.wait(lambda: d.call("live.status")["run"]["status"] == "stopped", timeout=20)
        d.wait(lambda: not _pid_alive(pid), timeout=10)      # terminated by mpvd (its own PID only)
        assert rx.wait(20) is not None
    finally:
        rx.stop()
