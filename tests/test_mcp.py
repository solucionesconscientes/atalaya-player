"""MCP stdio server (`python -m mpvd mcp`) against the test daemon + a headless mpv: handshake, tools/list, status,
play/pause/seek with auto-confirmation, add_note + notes resource, list_channels error path, and the real on-screen
confirmation flow (mu-menu dialog answered through mpv's IPC)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import PYTHON, start_mpv

ROOT = Path(__file__).resolve().parent.parent


class McpClient:
    def __init__(self, env: dict[str, str], extra: list[str] | None = None):
        self.proc = subprocess.Popen([str(PYTHON), "-m", "mpvd", "mcp", *(extra or [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                                     env={**os.environ, **env}, cwd=str(ROOT))
        self.n = 0

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self.n += 1
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"mcp server exited: {self.proc.stderr.read() if self.proc.stderr else ''}")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                return msg
        raise TimeoutError(method)

    def notify(self, method: str, params: dict | None = None) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()

    def tool(self, name: str, args: dict | None = None, timeout: float = 60.0) -> dict:
        r = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        assert "result" in r, r
        res = r["result"]
        return {"isError": res.get("isError"), "data": res.get("structuredContent") or json.loads(res["content"][0]["text"])}

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def mcp_env(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1",
                                           "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def test_mcp_handshake_tools_and_player_control(mcp_env, media_dir):
    h, d = mcp_env
    c = McpClient({**d.env, "MPVD_MCP_AUTOCONFIRM": "1"})
    try:
        init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "pytest", "version": "1"}})
        assert init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["serverInfo"]["name"] == "mpv-uos"
        assert "tools" in init["result"]["capabilities"] and init["result"]["instructions"]
        c.notify("notifications/initialized")
        assert c.request("ping")["result"] == {}
        tools = c.request("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        assert names == ["status", "play", "pause", "resume", "seek", "search_dialogue", "list_channels", "play_channel",
                         "download", "add_note", "subtitles_ai"]
        assert all("inputSchema" in t and t["inputSchema"]["type"] == "object" for t in tools)
        unknown = c.request("tools/call", {"name": "nope", "arguments": {}})
        assert unknown["result"]["isError"] is True

        # status with an idle player
        st = c.tool("status")
        assert not st["isError"] and len(st["data"]["sessions"]) == 1 and st["data"]["player"]["idle-active"] is True

        # play (nothing playing → no confirmation needed), then pause/resume/seek (auto-confirmed)
        r = c.tool("play", {"target": str(media_dir / "video30.mkv")})
        assert r["data"]["confirmed"] is True
        h.wait_property("path", lambda v: bool(v) and v.endswith("video30.mkv"), timeout=20)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 1, timeout=20)
        assert c.tool("resume")["data"] == {"pause": False}
        h.wait_property("pause", lambda v: v is False, timeout=5)
        assert c.tool("pause")["data"] == {"pause": True}
        h.wait_property("pause", lambda v: v is True, timeout=5)
        r = c.tool("seek", {"seconds": 12, "mode": "absolute"})
        assert r["data"]["confirmed"] is True
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 11.5 < v < 13.5, timeout=10)
        st = c.tool("status")["data"]["player"]
        assert st["path"].endswith("video30.mkv") and 11 < st["time-pos"] < 14 and st["pause"] is True
        # a second play while something plays replaces it (auto-confirmed here)
        r = c.tool("play", {"target": str(media_dir / "voz_es.flac"), "mode": "append"})
        assert r["data"]["mode"] == "append" and h.wait_property("playlist-count", lambda v: v == 2, timeout=10)

        # notes: written with a time link, listed as a resource, readable
        n = c.tool("add_note", {"text": "Aquí empieza la escena buena"})
        assert not n["isError"] and n["data"]["file"].endswith(".md") and n["data"]["time_pos"] > 11
        md = Path(n["data"]["file"]).read_text(encoding="utf-8")
        assert "video30.mkv" in md and "mpv-uos://open?path=" in md and "&t=1" in md and "escena buena" in md
        res = c.request("resources/list")["result"]["resources"]
        note_uri = next(r["uri"] for r in res if r["uri"].startswith("mpv://notes/"))
        read = c.request("resources/read", {"uri": note_uri})["result"]["contents"][0]
        assert read["mimeType"] == "text/markdown" and "escena buena" in read["text"]
        assert c.request("prompts/list")["result"] == {"prompts": []}

        # errors are reported as isError, not protocol errors
        bad = c.tool("seek", {"seconds": 1, "mode": "sideways"})
        assert bad["isError"] is True and "mode" in bad["data"]["error"]
        bad = c.tool("play_channel", {"id": "does-not-exist"})
        assert bad["isError"] is True
        # search_dialogue with no transcription: reports and (whisper vendored) starts one in the background
        sd = c.tool("search_dialogue", {"query": "zorro", "path": str(media_dir / "voz_es.flac")})
        assert not sd["isError"] and sd["data"]["hits"] == [] and "transcription" in sd["data"]
        assert h.script_errors() == []
    finally:
        c.close()


def test_mcp_confirmation_dialog(mcp_env, media_dir):
    h, d = mcp_env
    c = McpClient(d.env)   # no auto-confirm: the viewer must answer in the uosc dialog
    try:
        c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        c.notify("notifications/initialized")
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 1, timeout=20)
        # seek needs confirmation: answer "yes" through the dialog's callback, as uosc would
        import threading
        result: dict = {}

        def ask():
            result.update(c.tool("seek", {"seconds": 20, "mode": "absolute"}, timeout=60))

        th = threading.Thread(target=ask)
        th.start()
        req = h.wait_property("user-data/mu/confirm_request", lambda v: bool(v) and v.get("token"), timeout=20)
        assert "Saltar a 20 s" in req["text"]
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-confirm", timeout=10)
        h.command("script-message-to", "mu_menu", "mu-menu-confirm-event",
                  json.dumps({"type": "activate", "value": {"token": req["token"], "answer": "yes"}}))
        th.join(timeout=60)
        assert result["data"]["confirmed"] is True
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 19.5 < v < 21.5, timeout=10)
        # …and a refusal
        result.clear()

        def ask2():
            result.update(c.tool("seek", {"seconds": 5, "mode": "absolute"}, timeout=60))

        th = threading.Thread(target=ask2)
        th.start()
        req2 = h.wait_property("user-data/mu/confirm_request", lambda v: bool(v) and v.get("token") != req["token"], timeout=20)
        h.command("script-message-to", "mu_menu", "mu-menu-confirm-event",
                  json.dumps({"type": "activate", "value": {"token": req2["token"], "answer": "no"}}))
        th.join(timeout=60)
        assert result["data"]["confirmed"] is False
        assert h.get("time-pos") > 15
        assert h.script_errors() == []
    finally:
        c.close()
