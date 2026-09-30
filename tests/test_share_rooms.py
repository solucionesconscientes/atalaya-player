"""H25 · room logic of «Compartir» (no I/O): ids and tokens, expiry, failed attempts per address and per room,
guest names, per-room signed cookies, permissions (view → request → control, deny, revoke, kick)."""

from __future__ import annotations

import pytest

from mpvd.share import rooms
from mpvd.share.rooms import AttemptLimiter, JoinError, Room, clean_name


def test_room_ids_tokens_and_link():
    a, b = Room.new("s1", now=1000.0), Room.new("s1", now=1000.0)
    assert a.id != b.id and a.token != b.token and a.secret != b.secret
    assert len(a.token) >= 32 and len(a.id) == 8
    assert a.link_path() == f"/s/{a.id}#k={a.token}"
    assert a.expires_at == 1000.0 + rooms.ROOM_TTL
    assert Room.new(ttl=1, now=0).expires_at == rooms.MIN_TTL        # clamped
    assert Room.new(ttl=10**9, now=0).expires_at == rooms.MAX_TTL
    old = a.token
    assert a.rotate() != old and not a.check_token(old) and a.check_token(a.token)


def test_clean_name():
    assert clean_name("  Ana   María ") == "Ana María"
    assert clean_name("<b>Luis</b>{\\an8}") == "bLuis/ban8"  # markup and ASS braces gone
    assert len(clean_name("x" * 100)) == rooms.NAME_MAX
    assert clean_name("ana", {"Ana"}) == "ana (2)"
    assert clean_name("Ana", {"Ana", "Ana (2)"}) == "Ana (3)"
    with pytest.raises(ValueError):
        clean_name("   \x00 ")


def test_join_expiry_and_close():
    r = Room.new(now=0.0, ttl=3600)
    g = r.join(r.token, "Ana", "10.0.0.2", now=10.0)
    assert g.perm == rooms.PERM_VIEW and not g.pending and g.name == "Ana"
    assert r.join(r.token, "Ana", "10.0.0.3", now=11.0).name == "Ana (2)"
    with pytest.raises(JoinError) as e:
        r.join(r.token, "Luis", "10.0.0.4", now=3600.0)
    assert e.value.status == 410
    r2 = Room.new(now=0.0)
    r2.closed = True
    with pytest.raises(JoinError) as e:
        r2.join(r2.token, "Luis", "1.1.1.1", now=1.0)
    assert e.value.status == 410
    with pytest.raises(JoinError) as e:
        Room.new(now=0.0).join(Room.new().token, "", "x", now=1.0)
    assert e.value.status == 403  # wrong token is checked before the name


def test_empty_name_and_full_room():
    r = Room.new(now=0.0)
    with pytest.raises(JoinError) as e:
        r.join(r.token, "  ", "ip", now=1.0)
    assert e.value.status == 400
    r.max_guests = 2
    r.join(r.token, "a", "ip", now=1.0)
    r.join(r.token, "b", "ip", now=1.0)
    with pytest.raises(JoinError) as e:
        r.join(r.token, "c", "ip", now=1.0)
    assert e.value.status == 403 and "llena" in e.value.message


def test_attempt_limits_per_ip_and_per_room():
    lim = AttemptLimiter(max_fails=3, window=60, block=100)
    r = Room.new(now=0.0)
    for i in range(2):
        with pytest.raises(JoinError) as e:
            r.join("nope", "x", "10.0.0.9", lim, now=1.0 + i)
        assert e.value.status == 403
    with pytest.raises(JoinError) as e:
        r.join("nope", "x", "10.0.0.9", lim, now=3.0)
    assert e.value.status == 429
    with pytest.raises(JoinError) as e:          # blocked even with the right token
        r.join(r.token, "x", "10.0.0.9", lim, now=50.0)
    assert e.value.status == 429
    assert r.join(r.token, "x", "10.0.0.9", lim, now=104.0).name == "x"   # block over
    assert r.join(r.token, "y", "10.0.0.8", lim, now=104.0)             # other address unaffected
    # the window forgets old failures
    lim2 = AttemptLimiter(max_fails=2, window=10, block=100)
    assert lim2.fail("a", now=0.0) is False and lim2.fail("a", now=20.0) is False
    lim2.prune(now=40.0)
    # per room: after MAX_FAILS_ROOM wrong tokens the link stops working for everybody until rotate()
    r = Room.new(now=0.0)
    for i in range(rooms.MAX_FAILS_ROOM):
        with pytest.raises(JoinError):
            r.join("bad", "x", f"10.1.{i}.1", now=1.0)
    assert r.locked
    with pytest.raises(JoinError) as e:
        r.join(r.token, "x", "10.9.9.9", now=2.0)
    assert e.value.status == 403 and "nuevo" in e.value.message
    r.rotate()
    assert not r.locked and r.join(r.token, "x", "10.9.9.9", now=3.0)


def test_cookies_are_per_room_and_die_with_it():
    r, other = Room.new(), Room.new()
    g = r.join(r.token, "Ana", "ip")
    raw = r.cookie_value(g.id)
    assert r.guest_from_cookie(raw) is g
    assert other.guest_from_cookie(raw) is None
    assert r.guest_from_cookie(raw[:-2] + ("00" if not raw.endswith("00") else "11")) is None
    assert r.guest_from_cookie("") is None and r.guest_from_cookie(g.id) is None
    r.kick(g.id)
    assert r.guest_from_cookie(raw) is None
    g2 = r.join(r.token, "Luis", "ip")
    raw2 = r.cookie_value(g2.id)
    r.closed = True
    assert r.guest_from_cookie(raw2) is None


def test_permissions_request_grant_revoke_deny_kick():
    r = Room.new()
    g = r.join(r.token, "Ana", "ip")
    assert not g.can_control
    assert r.request_control(g.id) is True and g.pending
    assert r.request_control(g.id) is False          # already asked
    assert [x.id for x in r.pending()] == [g.id]
    r.set_perm(g.id, rooms.PERM_CONTROL)
    assert g.can_control and not g.pending and r.pending() == []
    assert r.request_control(g.id) is False          # already has it
    r.set_perm(g.id, rooms.PERM_VIEW)
    assert not g.can_control
    r.request_control(g.id)
    r.deny(g.id)
    assert not g.pending and g.perm == rooms.PERM_VIEW
    with pytest.raises(ValueError):
        r.set_perm(g.id, "admin")
    r.set_perm(g.id, rooms.PERM_CONTROL)
    r.kick(g.id)
    assert g.kicked and not g.can_control and r.active_guests() == []
    with pytest.raises(KeyError):
        r.set_perm(g.id, rooms.PERM_CONTROL)
    pub = r.public()
    assert pub["guests"] == [] and pub["id"] == r.id and pub["expires_in"] > 0


# -- H25 (3): public «solo ver» rooms, chat and reactions -----------------------------------------------------------


def test_public_room_anonymous_viewers_limit_and_no_control():
    r = Room.new(now=0.0, mode=rooms.MODE_PUBLIC, max_viewers=2)
    assert r.public_mode and r.max_guests == 2
    assert r.link_path() == f"/s/{r.id}#k={r.token}&v=1"
    a = r.join(r.token, "<b>Ana</b>", "10.0.0.2", now=1.0)
    b = r.join(r.token, "", "10.0.0.3", now=1.0)
    assert (a.name, b.name) == ("Espectador 1", "Espectador 2")   # names are not asked (nor kept)
    a.streams = b.streams = 1
    with pytest.raises(JoinError) as e:
        r.join(r.token, "", "10.0.0.4", now=100.0)
    assert e.value.status == 403 and "llena" in e.value.message
    # a viewer who closed the page long ago gives the seat away; one still watching keeps it
    b.streams, b.last_seen = 0, 10.0
    c = r.join(r.token, "", "10.0.0.4", now=100.0)
    assert c.name == "Espectador 3" and b.id not in r.guests and a.id in r.guests
    # same attempt limits as private rooms
    lim = AttemptLimiter(max_fails=2, window=60, block=100)
    with pytest.raises(JoinError):
        r.join("bad", "", "10.9.0.1", lim, now=101.0)
    with pytest.raises(JoinError) as e:
        r.join("bad", "", "10.9.0.1", lim, now=102.0)
    assert e.value.status == 429
    # no control in a public room
    with pytest.raises(PermissionError):
        r.request_control(a.id)
    with pytest.raises(ValueError):
        r.set_perm(a.id, rooms.PERM_CONTROL)
    r.set_perm(a.id, rooms.PERM_VIEW)
    pub = r.public()
    assert pub["mode"] == "public" and pub["chat"] is False and pub["guests"] == [] and pub["viewers"] == 1
    # the maximum is clamped; expiry works as in private rooms
    assert Room.new(mode=rooms.MODE_PUBLIC, max_viewers=10**6).max_guests == rooms.MAX_VIEWERS
    assert Room.new(mode=rooms.MODE_PUBLIC, max_viewers=0).max_guests == 1
    assert Room.new(mode=rooms.MODE_PRIVATE, max_viewers=90).max_guests == rooms.MAX_GUESTS
    with pytest.raises(ValueError):
        Room.new(mode="secret")
    old = Room.new(now=0.0, ttl=3600, mode=rooms.MODE_PUBLIC)
    with pytest.raises(JoinError) as e:
        old.join(old.token, "", "ip", now=3600.0)
    assert e.value.status == 410


def test_clean_chat_and_rate_windows():
    assert rooms.clean_chat("  hola\n\n  qué   tal ") == "hola qué tal"
    assert rooms.clean_chat("<img src=x onerror=alert(1)>") == "<img src=x onerror=alert(1)>"  # kept: shown as text
    assert rooms.clean_chat("a\x00b‮c\x1b[31m") == "a b c [31m"   # control and bidi characters out
    assert rooms.clean_chat("x" * rooms.CHAT_MAX) == "x" * rooms.CHAT_MAX
    for bad in ("", "   ", "\n\t", None, "x" * (rooms.CHAT_MAX + 1)):
        with pytest.raises(ValueError):
            rooms.clean_chat(bad)
    times: list[float] = []
    assert all(rooms.rate_ok(times, 3, window=10, now=t) for t in (0.0, 1.0, 2.0))
    assert rooms.rate_ok(times, 3, window=10, now=5.0) is False      # 3 in the last 10 s
    assert rooms.rate_ok(times, 3, window=10, now=10.5) is True       # the first one left the window
    assert set(rooms.REACTIONS) == {"like", "love", "laugh", "wow", "clap", "sad"}


def test_the_link_survives_honest_typos_spread_over_time():
    """H34 · `fails` no caducaba nunca: 20 erratas a lo largo de una tarde mataban una invitación aún válida."""
    room = Room.new(ttl=rooms.MAX_TTL, now=1000.0)
    bad = "x" * 20
    for i in range(rooms.MAX_FAILS_ROOM - 1):        # una cada minuto: nunca hay 20 dentro de la ventana
        with pytest.raises(JoinError):
            room.join(bad, "Ana", "192.168.1.9", None, now=1000.0 + i * 60)
    assert not room.locked and room.fails <= rooms.FAIL_WINDOW / 60 + 1

    # otra mucho después: las viejas ya no cuentan
    with pytest.raises(JoinError):
        room.join(bad, "Ana", "192.168.1.9", None, now=20000.0)
    assert not room.locked and room.fails == 1

    # entrar con el enlace bueno reinicia la cuenta
    for _ in range(3):
        with pytest.raises(JoinError):
            room.join(bad, "Ana", "192.168.1.9", None, now=20001.0)
    assert room.fails == 4
    room.join(room.token, "Ana", "192.168.1.9", None, now=20002.0)
    assert room.fails == 0 and room.fail_times == []

    # y la protección sigue: 20 intentos seguidos sí cierran el enlace
    for _ in range(rooms.MAX_FAILS_ROOM):
        with pytest.raises(JoinError):
            room.join(bad, "Bea", "192.168.1.10", None, now=20003.0)
    assert room.locked


def test_a_private_room_frees_the_seats_of_guests_who_left():
    """H34 · la limpieza de plazas estaba condicionada a las salas públicas: en privado la sala se llenaba de fantasmas."""
    room = Room.new(ttl=3600.0, now=1000.0)
    room.max_guests = 2
    a = room.join(room.token, "Ana", "192.168.1.9", None, now=1000.0)
    room.join(room.token, "Bea", "192.168.1.10", None, now=1000.0)
    with pytest.raises(JoinError, match="llena"):
        room.join(room.token, "Caro", "192.168.1.11", None, now=1001.0)

    a.streams = 0            # Ana closed the page long ago and never said goodbye
    later = 1000.0 + rooms.STALE_SECONDS + 5
    caro = room.join(room.token, "Caro", "192.168.1.11", None, now=later)
    assert caro.name == "Caro" and a.id not in room.guests
