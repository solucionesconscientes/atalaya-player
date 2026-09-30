"""H25 · a real browser in the room (optional: needs Google Chrome/Chromium and node >= 22). Headless Chrome with a
throwaway profile under tmp/ (closed by its own PID/process group) opens the invitation link, types a name, plays the
relay (native HLS or the vendored hls.js) and follows the host: playing in step (< 1 s), a seek and a pause, and
the «room closed» message. Nothing is downloaded."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import TMP
from tests.test_share_http import clip, share_env  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
CHROME = shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium")


def _node_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    try:
        return int(out.lstrip("v").split(".")[0]) >= 22
    except ValueError:
        return False


pytestmark = pytest.mark.skipif(not CHROME or not _node_ok(), reason="sin Chrome/Chromium o node >= 22")


class Browser:
    def __init__(self):
        self.profile = TMP / "chrome-profiles" / uuid.uuid4().hex[:8]
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
             "--autoplay-policy=no-user-gesture-required", "--mute-audio", "--disable-background-networking",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not port_file.exists() or not port_file.read_text().strip():
            if time.monotonic() > deadline or self.proc.poll() is not None:
                self.close()
                pytest.skip("el navegador no arrancó")
            time.sleep(0.1)
        port = port_file.read_text().split()[0]
        self.bridge = subprocess.Popen(["node", str(ROOT / "tests" / "cdp_bridge.js"), port], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, text=True, start_new_session=True)
        assert json.loads(self.bridge.stdout.readline()).get("ready"), "CDP bridge"

    def _call(self, cmd: dict):  # type: ignore[no-untyped-def]
        assert self.bridge.stdin is not None and self.bridge.stdout is not None
        self.bridge.stdin.write(json.dumps(cmd) + "\n")
        self.bridge.stdin.flush()
        out = json.loads(self.bridge.stdout.readline())
        if "error" in out or "fatal" in out:
            raise AssertionError(out)
        return out["value"]

    def nav(self, url: str) -> None:
        self._call({"nav": url})

    def js(self, expr: str):  # type: ignore[no-untyped-def]
        return self._call({"eval": expr})

    def wait(self, expr: str, timeout: float = 30.0):  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.js(expr)
            except AssertionError as exc:
                last = exc
            if last and not isinstance(last, AssertionError):
                return last
            time.sleep(0.2)
        raise AssertionError(f"navegador: {expr} → {last}; página: {self.snapshot()}")

    def snapshot(self):  # type: ignore[no-untyped-def]
        try:
            return self.js("JSON.stringify({ov: document.getElementById('overlay-text').textContent, "
                           "msg: document.getElementById('message-text').textContent, "
                           "ct: document.getElementById('video').currentTime, p: document.getElementById('video').paused, "
                           "rs: document.getElementById('video').readyState, src: document.getElementById('video').currentSrc, "
                           "err: (document.getElementById('video').error||{}).message})")
        except AssertionError as exc:
            return str(exc)

    def close(self) -> None:
        for p in (getattr(self, "bridge", None), self.proc):
            if p is None or p.poll() is not None:
                continue
            try:
                os.killpg(p.pid, signal.SIGTERM)  # our own process group (start_new_session): Chrome's children too
                p.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        shutil.rmtree(self.profile, ignore_errors=True)


VIDEO = "document.getElementById('video')"


@pytest.mark.parametrize("engine", ["auto", "hlsjs"])
def test_browser_guest_follows_the_host(share_env, clip, engine):  # noqa: F811
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    url = d.call("share.create")["url"]
    if engine == "hlsjs":
        url = url.replace("#k=", "?hlsjs=1#k=")
    b = Browser()
    try:
        b.nav(url)
        b.wait("!document.getElementById('join').classList.contains('hidden')")
        b.js("document.getElementById('name').value = 'Navegador'; "
             "document.getElementById('join-form').requestSubmit(); true")
        b.wait("!document.getElementById('room').classList.contains('hidden')")
        assert b.js("location.hash") == ""  # the token leaves the address bar
        assert d.call("share.status")["guests"][0]["name"] == "Navegador"
        b.wait(f"{VIDEO}.readyState >= 1", timeout=40)
        used = b.js("window.Hls ? 'hls.js' : 'nativo'")
        if engine == "hlsjs":
            assert used == "hls.js"
        h.command("set", "pause", "no")
        b.wait(f"!{VIDEO}.paused && {VIDEO}.currentTime > 2", timeout=40)
        time.sleep(3)  # let the drift correction settle
        diffs = []
        for _ in range(5):
            host = h.get("time-pos")
            guest = b.js(f"{VIDEO}.currentTime")
            diffs.append(abs(host - guest))
            time.sleep(0.5)
        assert min(diffs) < 1.0, (diffs, used)
        h.command("seek", "25", "absolute")
        b.wait(f"Math.abs({VIDEO}.currentTime - 26) < 2", timeout=20)
        h.command("set", "pause", "yes")
        b.wait(f"{VIDEO}.paused", timeout=10)
        host = h.get("time-pos")
        b.wait(f"Math.abs({VIDEO}.currentTime - {host}) < 0.5", timeout=10)
        d.call("share.close")
        b.wait("document.getElementById('message-text').textContent.includes('cerrado la sala')", timeout=10)
        print(f"motor HLS del navegador: {used}; desfase: {min(diffs):.2f} s")
    finally:
        b.close()
