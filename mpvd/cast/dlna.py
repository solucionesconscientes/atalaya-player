"""DLNA / UPnP AV «MediaRenderer» control (H27 «Enviar a la tele»): SSDP discovery, the device description and the
AVTransport / RenderingControl SOAP actions. Standard library only.

* Discovery: ``M-SEARCH`` to 239.255.255.250:1900 with ``ST: urn:schemas-upnp-org:device:MediaRenderer:1``; every
  answer carries ``LOCATION`` = the device description (XML). ``MPV_UOS_SSDP_ADDR=host:port`` sends the search there
  instead (tests: a fake renderer on loopback, where multicast is not reliable).
* Description: ``friendlyName``, ``manufacturer``, ``modelName`` and the ``controlURL`` of AVTransport:1 and
  RenderingControl:1 (relative to ``URLBase`` or to LOCATION).
* Actions (UPnP AVTransport:1): SetAVTransportURI (with DIDL-Lite metadata: many TVs refuse an empty one), Play,
  Pause, Stop, Seek (``REL_TIME`` ``H:MM:SS``), GetPositionInfo, GetTransportInfo; RenderingControl:1 SetVolume/GetVolume.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

log = logging.getLogger("mpvd.cast.dlna")

SSDP_ADDR = ("239.255.255.250", 1900)
ST_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RC = "urn:schemas-upnp-org:service:RenderingControl:1"
USER_AGENT = "Linux/1 UPnP/1.0 MPV-UOS/1"


class DlnaError(RuntimeError):
    pass


@dataclass
class Renderer:
    id: str                 # UDN (uuid:…) or LOCATION when there is none
    name: str
    location: str
    avtransport: str        # absolute control URL
    rendering: str = ""
    manufacturer: str = ""
    model: str = ""
    kind: str = "dlna"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def search_message(st: str = ST_RENDERER, mx: int = 2) -> bytes:
    return ("M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
            'MAN: "ssdp:discover"\r\n'
            f"MX: {mx}\r\n"
            f"ST: {st}\r\n"
            f"USER-AGENT: {USER_AGENT}\r\n\r\n").encode("ascii")


def parse_ssdp_response(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8", "replace")
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    if not lines or not re.match(r"^(HTTP/1\.[01] 200|NOTIFY )", lines[0]):
        return {}
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def ssdp_target() -> tuple[str, int]:
    env = os.environ.get("MPV_UOS_SSDP_ADDR", "")
    if env and ":" in env:
        host, port = env.rsplit(":", 1)
        return host, int(port)
    return SSDP_ADDR


class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.locations: dict[str, dict[str, str]] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        h = parse_ssdp_response(data)
        loc = h.get("location")
        if loc and loc.startswith("http"):
            self.locations.setdefault(loc, h)


async def discover_locations(timeout: float = 3.0, st: str = ST_RENDERER) -> dict[str, dict[str, str]]:
    """LOCATION → SSDP headers of every renderer that answers within ``timeout``."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.bind(("", 0))
    transport, proto = await loop.create_datagram_endpoint(_Collector, sock=sock)
    try:
        target = ssdp_target()
        msg = search_message(st, mx=max(1, min(5, int(timeout))))
        for _ in range(2):              # UDP: say it twice
            transport.sendto(msg, target)
            await asyncio.sleep(min(0.3, timeout / 4))
        await asyncio.sleep(max(0.0, timeout - 0.6))
    finally:
        transport.close()
    return proto.locations


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_description(xml: bytes | str, location: str) -> Renderer | None:
    """The renderer described at ``location`` (None if it has no AVTransport)."""
    root = ET.fromstring(xml)
    base = ""
    for el in root.iter():
        if _local(el.tag) == "URLBase" and el.text:
            base = el.text.strip()
    base = base or location
    found: Renderer | None = None
    for dev in root.iter():
        if _local(dev.tag) != "device":
            continue
        services: dict[str, str] = {}
        for svc_list in dev:
            if _local(svc_list.tag) != "serviceList":
                continue
            for svc in svc_list:
                stype, ctrl = _find_text(svc, "serviceType"), _find_text(svc, "controlURL")
                if stype and ctrl:
                    services[stype.rsplit(":", 1)[0]] = urllib.parse.urljoin(base, ctrl)
        avt = services.get(AVT.rsplit(":", 1)[0])
        if avt and found is None:
            found = Renderer(id=_find_text(dev, "UDN") or location, name=_find_text(dev, "friendlyName") or "Tele",
                             location=location, avtransport=avt, rendering=services.get(RC.rsplit(":", 1)[0], ""),
                             manufacturer=_find_text(dev, "manufacturer"), model=_find_text(dev, "modelName"))
    return found


def _http(url: str, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 5.0) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - LAN device URLs from SSDP
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        m = re.search(rb"<errorDescription>(.*?)</errorDescription>", body or b"")
        code = re.search(rb"<errorCode>(\d+)</errorCode>", body or b"")
        detail = (m.group(1).decode("utf-8", "replace") if m else "") or f"HTTP {exc.code}"
        raise DlnaError(f"la tele rechazó la orden ({detail}{' ' + code.group(1).decode() if code else ''})") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise DlnaError(f"la tele no responde ({exc})") from exc


async def fetch_renderer(location: str) -> Renderer | None:
    xml = await asyncio.to_thread(_http, location)
    try:
        return parse_description(xml, location)
    except ET.ParseError as exc:
        log.info("bad description at %s: %s", location, exc)
        return None


async def discover(timeout: float = 3.0) -> list[Renderer]:
    locations = await discover_locations(timeout)
    out: dict[str, Renderer] = {}
    results = await asyncio.gather(*(fetch_renderer(loc) for loc in locations), return_exceptions=True)
    for r in results:
        if isinstance(r, Renderer):
            out.setdefault(r.id, r)
        elif isinstance(r, Exception):
            log.info("renderer description failed: %s", r)
    return sorted(out.values(), key=lambda r: r.name.lower())


# -- SOAP -----------------------------------------------------------------------------------------------------------

def soap_envelope(service: str, action: str, args: list[tuple[str, str]]) -> bytes:
    inner = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in args)
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}">{inner}</u:{action}>'
            "</s:Body></s:Envelope>").encode("utf-8")


def parse_soap_response(xml: bytes) -> dict[str, str]:
    root = ET.fromstring(xml)
    for el in root.iter():
        if _local(el.tag).endswith("Response"):
            return {_local(c.tag): (c.text or "") for c in el}
    return {}


async def soap(url: str, service: str, action: str, args: list[tuple[str, str]], timeout: float = 6.0) -> dict[str, str]:
    body = soap_envelope(service, action, args)
    headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{service}#{action}"'}
    data = await asyncio.to_thread(_http, url, body, headers, timeout)
    try:
        return parse_soap_response(data)
    except ET.ParseError:
        return {}


def hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def parse_hms(text: str) -> float | None:
    m = re.match(r"^(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?$", (text or "").strip())
    if not m:
        return None
    frac = float("0." + m.group(4)) if m.group(4) else 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + frac


def didl(url: str, title: str, mime: str, seekable: bool, duration: float | None = None) -> str:
    """DIDL-Lite of one item. ``DLNA.ORG_OP=01`` = byte seeks allowed (a file); ``00`` for a live stream."""
    op = "01" if seekable else "00"
    flags = "01700000000000000000000000000000" if seekable else "01300000000000000000000000000000"
    upnp_class = "object.item.audioItem.musicTrack" if mime.startswith("audio/") else "object.item.videoItem"
    dur = f' duration="{hms(duration)}.000"' if duration else ""
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(title)}</dc:title>'
            f"<upnp:class>{upnp_class}</upnp:class>"
            f'<res protocolInfo="http-get:*:{mime}:DLNA.ORG_OP={op};DLNA.ORG_CI=0;DLNA.ORG_FLAGS={flags}"{dur}>'
            f"{escape(url)}</res></item></DIDL-Lite>")


class Controller:
    """AVTransport/RenderingControl of one renderer."""

    def __init__(self, renderer: Renderer):
        self.r = renderer

    async def load(self, url: str, title: str, mime: str, seekable: bool, duration: float | None = None) -> None:
        with_meta = [("InstanceID", "0"), ("CurrentURI", url),
                     ("CurrentURIMetaData", didl(url, title, mime, seekable, duration))]
        try:
            await self.stop()
        except DlnaError:
            pass                       # nothing loaded yet: some TVs answer Stop with an error
        await soap(self.r.avtransport, AVT, "SetAVTransportURI", with_meta)

    async def play(self) -> None:
        await soap(self.r.avtransport, AVT, "Play", [("InstanceID", "0"), ("Speed", "1")])

    async def pause(self) -> None:
        await soap(self.r.avtransport, AVT, "Pause", [("InstanceID", "0")])

    async def stop(self) -> None:
        await soap(self.r.avtransport, AVT, "Stop", [("InstanceID", "0")])

    async def seek(self, seconds: float) -> None:
        await soap(self.r.avtransport, AVT, "Seek", [("InstanceID", "0"), ("Unit", "REL_TIME"), ("Target", hms(seconds))])

    async def position(self) -> dict[str, Any]:
        info = await soap(self.r.avtransport, AVT, "GetPositionInfo", [("InstanceID", "0")])
        tr = await soap(self.r.avtransport, AVT, "GetTransportInfo", [("InstanceID", "0")])
        return {"position": parse_hms(info.get("RelTime", "")), "duration": parse_hms(info.get("TrackDuration", "")),
                "state": tr.get("CurrentTransportState", "")}

    async def set_volume(self, volume: int) -> None:
        if not self.r.rendering:
            raise DlnaError("esta tele no deja cambiar el volumen")
        await soap(self.r.rendering, RC, "SetVolume", [("InstanceID", "0"), ("Channel", "Master"),
                                                        ("DesiredVolume", str(max(0, min(100, int(volume)))))])
