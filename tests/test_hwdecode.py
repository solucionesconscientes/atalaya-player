"""H31: hardware decoding from vainfo, labels per format, the -S codec limit and the playback format."""

from __future__ import annotations

import json
import re

from mpvd import hwdecode as hwd
from mpvd.ytdl import info
from tests.conftest import ROOT

# `vainfo` on the development laptop (Intel iHD 26.1.2): decode = MPEG-2, H.264, JPEG, VP8, HEVC Main; encode H.264 LP
VAINFO_IHD = """vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 26.1.2 ()
vainfo: Supported profile and entrypoints
      VAProfileNone                   :	VAEntrypointVideoProc
      VAProfileNone                   :	VAEntrypointStats
      VAProfileMPEG2Simple            :	VAEntrypointVLD
      VAProfileMPEG2Main              :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointEncSliceLP
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointEncSliceLP
      VAProfileJPEGBaseline           :	VAEntrypointVLD
      VAProfileH264ConstrainedBaseline:	VAEntrypointVLD
      VAProfileVP8Version0_3          :	VAEntrypointVLD
      VAProfileHEVCMain               :	VAEntrypointVLD
"""
# a newer chip (Tiger Lake and later): VP9 8/10 bits, HEVC Main10, AV1; one encode-only profile that must not count
VAINFO_NEW = """      VAProfileHEVCMain10             :	VAEntrypointVLD
      VAProfileVP9Profile0            :	VAEntrypointVLD
      VAProfileVP9Profile2            :	VAEntrypointVLD
      VAProfileAV1Profile0            :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileHEVCMain444            :	VAEntrypointEncSliceLP
"""


def test_parse_vainfo_decode_entrypoints_only():
    assert hwd.parse_vainfo(VAINFO_IHD) == {"mpeg2", "h264", "vp8", "hevc"}
    assert hwd.parse_vainfo(VAINFO_NEW) == {"hevc10", "vp9", "vp9_10", "av1", "h264"}
    assert hwd.parse_vainfo("vainfo: error: can't connect to X server!") == set()


def test_codec_keys_and_classification():
    assert hwd.codec_key("avc1.64002a") == "h264" and hwd.codec_key("h264") == "h264"
    assert hwd.codec_key("vp09.00.51.08") == "vp9" and hwd.codec_key("vp09.02.51.10") == "vp9_10"
    assert hwd.codec_key("vp9") == "vp9" and hwd.codec_key("av01.0.09M.08") == "av1"
    assert hwd.codec_key("hvc1.2.4.L153") == "hevc10" and hwd.codec_key("hev1.1.6.L93") == "hevc"
    assert hwd.codec_key("none") is None and hwd.codec_key(None) is None
    ihd = hwd.parse_vainfo(VAINFO_IHD)
    assert hwd.classify(ihd, "avc1.64002a") == "hw" and hwd.classify(ihd, "av01.0.09M.08") == "cpu"
    assert hwd.classify(ihd, "vp09.00.51.08") == "cpu" and hwd.classify(None, "avc1") is None
    new = hwd.parse_vainfo(VAINFO_NEW)
    assert hwd.classify(new, "hev1.1.6.L93") == "hw"   # a Main10 decoder takes 8 bits too
    assert hwd.classify(new, "vp09.00.51.08") == "hw"


def test_sort_limit_and_playback_format():
    assert hwd.sort_codec(hwd.parse_vainfo(VAINFO_IHD)) == "h265"
    assert hwd.sort_codec(hwd.parse_vainfo(VAINFO_NEW)) == "av01"
    assert hwd.sort_codec(None) == "h264" and hwd.sort_codec(set()) == "h264"
    fmt = hwd.playback_format({"av1", "h264"})
    parts = fmt.split("/")
    assert parts[0] == "bestvideo[height<=?1080][vcodec^=av01]+bestaudio"
    assert parts[1] == "bestvideo[height<=?1080][vcodec~='^(avc1|h264)']+bestaudio" and parts[-1] == "best"
    assert hwd.playback_format(None).split("/")[0].endswith("[vcodec~='^(avc1|h264)']+bestaudio")


def test_env_override(monkeypatch):
    hwd.detect.cache_clear()
    try:
        monkeypatch.setenv("MPV_UOS_HWDECODE", "av1,h264")
        d = hwd.detect()
        assert d["source"] == "env" and d["hw"] == ["av1", "h264"] and d["sort"] == "av01"
        hwd.detect.cache_clear()
        monkeypatch.setenv("MPV_UOS_HWDECODE", "unknown")
        assert hwd.detect()["hw"] is None
    finally:
        hwd.detect.cache_clear()


def test_quality_rows_are_labelled():
    data = json.loads((ROOT / "tests" / "fixtures" / "ytdlp" / "youtube_bbb.json").read_text(encoding="utf-8"))
    a = info.analyze(data, hwd.parse_vainfo(VAINFO_IHD))
    rows = {r["id"]: r for group in a["formats"].values() for r in group}
    assert rows["401"]["hw"] == "cpu" and rows["401"]["hint"].endswith("exigente (por procesador)")
    avc = [r for r in rows.values() if str(r.get("vcodec") or "").startswith("avc1")]
    assert avc and all(r["hw"] == "hw" and "fluido en tu equipo" in r["hint"] for r in avc)
    audio = a["formats"]["audio"]
    assert audio and all(r.get("hw") is None and "equipo" not in r["hint"] for r in audio)
    assert all("hw" not in r for g in info.analyze(data)["formats"].values() for r in g)   # unknown machine


def test_mpv_conf_default_matches_mu_ytdl_factory_format():
    conf = (ROOT / "mpv-config" / "mpv.conf").read_text(encoding="utf-8")
    lua = (ROOT / "mpv-config" / "scripts" / "mu-ytdl" / "main.lua").read_text(encoding="utf-8")
    conf_fmt = re.search(r"^ytdl-format=(.*)$", conf, re.M).group(1)
    lua_fmt = re.search(r"^local FACTORY_FORMAT = '(.*)'$", lua, re.M).group(1)
    assert conf_fmt == lua_fmt
