"""The remote-control status tells the user when a host firewall will drop the phone, with the exact command."""

import subprocess

from mpvd.remote import service


def _fake_systemctl(active_unit):
    def run(args, **kw):
        unit = args[-1]
        return subprocess.CompletedProcess(args, 0 if unit == active_unit else 3,
                                           stdout="active\n" if unit == active_unit else "inactive\n", stderr="")
    return run


def test_ufw_hint_uses_the_lan_subnet(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(service.subprocess, "run", _fake_systemctl("ufw"))
    service._FIREWALL_CACHE.clear()
    hint = service.firewall_hint(8790, "10.0.7.23")
    assert hint["tool"] == "ufw" and hint["subnet"] == "10.0.7.0/24"
    assert hint["command"].startswith("sudo ufw allow from 10.0.7.0/24 to any port 8790 proto tcp")


def test_firewalld_and_no_firewall(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(service.subprocess, "run", _fake_systemctl("firewalld"))
    service._FIREWALL_CACHE.clear()
    assert "firewall-cmd --permanent --add-port=8790/tcp" in service.firewall_hint(8790, "192.168.1.5")["command"]
    monkeypatch.setattr(service.subprocess, "run", _fake_systemctl("none"))
    service._FIREWALL_CACHE.clear()
    assert service.firewall_hint(8790, "192.168.1.5") is None


def test_the_hint_is_cached_per_port_so_the_room_and_the_remote_do_not_fight(monkeypatch):
    """H34 · una sola entrada de caché hacía que mando, sala y tele se invalidaran entre sí y se llamase a systemctl casi siempre."""
    calls = []

    def fake_run(args, **kw):
        calls.append(tuple(args))
        import subprocess as sp
        return sp.CompletedProcess(args, 0, stdout="active\n", stderr="")

    monkeypatch.setattr(service.shutil, "which", lambda name: "/bin/systemctl")
    monkeypatch.setattr(service.subprocess, "run", fake_run)
    service._FIREWALL_CACHE.clear()

    a = service.firewall_hint(8790, "192.168.1.5", "mando")
    b = service.firewall_hint(8791, "192.168.1.5", "salas")
    n = len(calls)
    assert a["port"] == 8790 and b["port"] == 8791
    # both are cached at the same time: asking again costs nothing
    assert service.firewall_hint(8790, "192.168.1.5", "mando") == a
    assert service.firewall_hint(8791, "192.168.1.5", "salas") == b
    assert len(calls) == n


def test_the_cached_hint_never_blocks_the_event_loop():
    """firewall_cached devuelve al momento dentro del bucle y refresca en un hilo."""
    import asyncio
    import time as _time

    service._FIREWALL_CACHE.clear()
    service._FIREWALL_CACHE[(8790, "192.168.1.5", "mando")] = (_time.monotonic(), {"tool": "ufw", "port": 8790})

    async def go():
        t0 = _time.monotonic()
        got = service.firewall_cached(8790, "192.168.1.5")
        return got, _time.monotonic() - t0

    got, elapsed = asyncio.run(go())
    assert got["tool"] == "ufw" and elapsed < 0.05
