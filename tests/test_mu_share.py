"""H25 · mu-share headless: the «Compartir» menu with breadcrumbs, creating a room (QR over the video), the guests
list with give/take control, ⌫ back, the QR toggle, closing the room from the menu, and the room expiring by itself
(QR hidden, «La sala ha caducado»)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from tests.conftest import start_mpv
from tests.test_nav import press, wait_nav
from tests.test_share_http import MU_OPTS, Guest


@pytest.fixture
def mu_share(daemon_env, media_dir):
    daemon_env.extra_env.update({"MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PUBLIC_HOST": "127.0.0.1",
                                 "MPVD_SHARE_PORT": "0", "MPVD_SHARE_MIN_TTL": "1", "MPV_UOS_VAAPI": "0",
                                 "MPV_UOS_YTDLP_AUTO_UPDATE": "0"})
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes", str(media_dir / "video30.mkv")],
                  env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 1, timeout=20)
        yield h, daemon_env
    finally:
        h.stop()


def ev(h, event: dict, message: str = "mu-share-event") -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_share", message, json.dumps({**base, **event}))


def share(h, pred, timeout: float = 20.0) -> dict:
    return h.wait_property("user-data/mu/share", lambda v: bool(v) and pred(v), timeout=timeout)


def titles(v: dict) -> list[str]:
    return [i["title"] for i in v.get("items") or []]


def test_menu_create_guests_permissions_close(mu_share):
    h, d = mu_share
    h.command("script-binding", "mu_share/share-menu")
    wait_nav(h, "mu-share", "MPV-UOS › Compartir")
    v = share(h, lambda v: "Crear una sala para ver juntos" in titles(v))
    assert v["open"] is False and v["view"] == "root"

    ev(h, {"type": "activate", "index": 1, "value": {"action": "create"}})
    v = share(h, lambda v: v["open"] and v["qr_visible"])
    assert v["url"].startswith("http://127.0.0.1:") and "#k=" in v["url"] and v["qr_size"] >= 21
    assert v["lan_only"] is True
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)
    # the QR binding hides and shows it again (same room)
    h.command("script-binding", "mu_share/share-qr")
    share(h, lambda v: v["qr_visible"] is False)
    h.command("script-binding", "mu_share/share-qr")
    v = share(h, lambda v: v["qr_visible"] is True)
    url = v["url"]

    h.command("script-binding", "mu_share/share-menu")
    v = share(h, lambda v: "Cerrar la sala" in titles(v) and "Copiar el enlace" in titles(v))
    assert "Mostrar el enlace y el código QR" in titles(v) and "Invitados" in titles(v)

    # a guest joins from «the browser»: notice on the OSD, the guests list
    base, rest = url.split("/s/", 1)
    room, token = rest.split("#k=")
    ana = Guest(base, room)
    assert ana.req("api/join", {"token": token, "name": "Ana"})[0] == 200
    v = share(h, lambda v: v["last_notice"] == "Ana se ha unido" and len(v["guests"]) == 1)
    gid = v["guests"][0]["id"]
    ev(h, {"type": "activate", "index": 4, "value": {"view": "guests"}})
    wait_nav(h, "mu-share", "MPV-UOS › Compartir › Invitados")
    v = share(h, lambda v: v["view"] == "guests" and "Ana" in titles(v))
    assert v["items"][0]["hint"].startswith("solo ver")
    ev(h, {"type": "activate", "index": 2, "value": {"view": "guest", "id": gid}})
    wait_nav(h, "mu-share", "MPV-UOS › Compartir › Invitados › Ana")
    share(h, lambda v: v["view"] == "guest" and "Dar el control" in titles(v))
    ev(h, {"type": "activate", "index": 1, "value": {"perm": "control", "id": gid}})
    share(h, lambda v: v["view"] == "guest" and "Quitar el control" in titles(v))
    assert d.call("share.status")["guests"][0]["perm"] == "control"
    ev(h, {"type": "activate", "index": 1, "value": {"perm": "view", "id": gid}})
    share(h, lambda v: "Dar el control" in titles(v))
    assert d.call("share.status")["guests"][0]["perm"] == "view"
    # ⌫ goes back one level at a time
    press(h, "BS")
    share(h, lambda v: v["view"] == "guests" and v["depth"] == 2)
    press(h, "BS")
    share(h, lambda v: v["view"] == "root" and v["depth"] == 1)
    # a request while the menu is open: the yes/no menu, dismissed → still pending in the list
    assert ana.req("api/request", {})[0] == 200
    v = share(h, lambda v: v["asking"] == gid and v["pending"] == 1)
    h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-share-request", timeout=10)
    ev(h, {"type": "close"}, "mu-share-request-event")
    share(h, lambda v: v["asking"] == "")
    assert d.call("share.status")["pending"][0]["id"] == gid
    ev(h, {"type": "activate", "index": 1, "value": {"yes": True, "id": gid}}, "mu-share-request-event")
    d.wait(lambda: d.call("share.status")["guests"][0]["perm"] == "control", timeout=10)
    # close from the menu
    h.command("script-binding", "mu_share/share-menu")
    share(h, lambda v: "Cerrar la sala" in titles(v))
    ev(h, {"type": "activate", "index": 7, "value": {"action": "close"}})
    v = share(h, lambda v: v["open"] is False and v["qr_visible"] is False)
    share(h, lambda v: "Crear una sala para ver juntos" in titles(v))
    with pytest.raises(urllib.error.URLError):  # the share server stops with the room
        ana.req("api/me")
    h.command("script-message-to", "uosc", "close-menu", "mu-share")
    assert not h.script_errors(), h.script_errors()


def test_room_expires_by_itself(mu_share):
    h, d = mu_share
    res = d.call("share.create", {"ttl_hours": 3 / 3600})  # three seconds (MPVD_SHARE_MIN_TTL=1 in this test)
    assert res["room"]["expires_in"] <= 3
    h.command("script-binding", "mu_share/share-qr")
    share(h, lambda v: v["qr_visible"] and v["open"])
    v = share(h, lambda v: v["last_notice"] == "La sala ha caducado" and not v["qr_visible"], timeout=20)
    assert v["open"] is False and d.call("share.status")["open"] is False
    assert not h.script_errors(), h.script_errors()
