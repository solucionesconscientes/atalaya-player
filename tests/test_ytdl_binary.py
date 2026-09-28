"""yt-dlp binary resolution and the SHA-verified updater (against a local HTTP server)."""

import hashlib
import json
from pathlib import Path

import pytest

from mpvd.net import HttpCache
from mpvd.ytdl.binary import (
    SAFE_ARGS,
    JsRuntime,
    YtdlpBinary,
    YtdlpUpdater,
    find_ytdlp,
    parse_sums,
    version_newer,
)
from tests.test_mu_iptv import serve

FAKE = Path(__file__).parent / "fixtures" / "ytdlp" / "fake_ytdlp.py"


def test_parse_sums_and_version_order():
    sums = parse_sums("abc\n" + "a" * 64 + "  yt-dlp\n" + "B" * 64 + " *yt-dlp.exe\n")
    assert sums == {"yt-dlp": "a" * 64, "yt-dlp.exe": "b" * 64}
    assert version_newer("2026.09.01", "2026.08.19")
    assert version_newer("2026.08.19.1", "2026.08.19")
    assert not version_newer("2026.08.19", "2026.08.19")
    assert not version_newer("2026.08.01", "2026.08.19")
    assert not version_newer("garbage", "2026.08.19")
    assert version_newer("2026.08.19", "")


def test_find_ytdlp_prefers_env_then_vendor(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "vendor" / "bin").mkdir(parents=True)
    vendored = root / "vendor" / "bin" / "yt-dlp"
    vendored.write_text("#!/usr/bin/env python3\nprint('2026.01.01')\n")
    vendored.chmod(0o755)
    monkeypatch.delenv("MPV_UOS_YTDLP", raising=False)
    b = find_ytdlp(root)
    assert b is not None and b.path == vendored and b.source == "vendor"
    assert b.argv[0].endswith("python") or b.argv[0].endswith("python3")  # zipimport → run with our interpreter
    cmd = b.command("-J")
    assert cmd[2:2 + len(SAFE_ARGS)] == SAFE_ARGS and cmd[-1] == "-J"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    b = find_ytdlp(root)
    assert b.source == "env" and b.path == FAKE and b.version_sync() == "2026.08.19"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(tmp_path / "missing"))
    assert find_ytdlp(root).source == "vendor"  # bad env falls back


def test_js_runtime_args():
    b = YtdlpBinary(FAKE, "env", JsRuntime("node", "/usr/bin/node", "22"), ffmpeg="/usr/bin/ffmpeg")
    cmd = b.command("-J", "--", "u")
    assert cmd[cmd.index("--js-runtimes") + 1] == "node:/usr/bin/node"
    assert cmd[cmd.index("--ffmpeg-location") + 1] == "/usr/bin/ffmpeg"
    assert "--no-update" in cmd and "--no-remote-components" in cmd


@pytest.fixture
def release_server(tmp_path):
    asset = b"#!/usr/bin/env python3\nprint('2026.09.01')\n"
    sha = hashlib.sha256(asset).hexdigest()
    files = {"/asset/yt-dlp": asset, "/asset/SHA2-256SUMS": (sha + "  yt-dlp\n" + "0" * 64 + "  yt-dlp.exe\n").encode()}
    httpd = serve(files)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    files["/latest"] = json.dumps({"tag_name": "2026.09.01", "assets": [
        {"name": "yt-dlp", "browser_download_url": base + "/asset/yt-dlp"},
        {"name": "SHA2-256SUMS", "browser_download_url": base + "/asset/SHA2-256SUMS"},
    ]}).encode()
    files["/latest-bad"] = json.dumps({"tag_name": "2026.09.02", "assets": [
        {"name": "yt-dlp", "browser_download_url": base + "/asset/yt-dlp"},
        {"name": "SHA2-256SUMS", "browser_download_url": base + "/asset/SHA2-256SUMS-bad"},
    ]}).encode()
    files["/asset/SHA2-256SUMS-bad"] = ("f" * 64 + "  yt-dlp\n").encode()
    yield base, asset
    httpd.shutdown()


def test_updater_check_apply_and_verify(tmp_path, release_server):
    base, asset = release_server
    target = tmp_path / "vendor" / "bin" / "yt-dlp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    up = YtdlpUpdater(HttpCache(tmp_path / "http"), target, releases_url=base + "/latest")
    st = up.check("2026.08.19")
    assert st.update_available and st.latest == "2026.09.01" and not st.error
    st = up.apply()
    assert not st.error and st.installed == "2026.09.01" and not st.update_available and st.applied_at > 0
    assert target.read_bytes() == asset and target.stat().st_mode & 0o111
    assert (target.parent / "yt-dlp.version").read_text().strip() == "2026.09.01"
    # same version installed → nothing to do (cached release JSON, no network)
    assert up.check("2026.09.01").update_available is False

    bad = YtdlpUpdater(HttpCache(tmp_path / "http2"), target, releases_url=base + "/latest-bad")
    bad.check("2026.08.19")
    st = bad.apply()
    assert "SHA-256 mismatch" in st.error and target.read_bytes() == asset  # untouched


def test_updater_offline_reports_error(tmp_path):
    up = YtdlpUpdater(HttpCache(tmp_path / "http", offline=True), tmp_path / "yt-dlp", releases_url="http://127.0.0.1:9/x")
    st = up.check("2026.08.19")
    assert st.error and not st.update_available
    assert "no release information" in up.apply().error
