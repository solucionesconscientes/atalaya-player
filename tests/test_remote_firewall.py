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
    monkeypatch.setitem(service._FIREWALL_CACHE, "at", 0.0)
    hint = service.firewall_hint(8790, "10.0.7.23")
    assert hint["tool"] == "ufw" and hint["subnet"] == "10.0.7.0/24"
    assert hint["command"].startswith("sudo ufw allow from 10.0.7.0/24 to any port 8790 proto tcp")


def test_firewalld_and_no_firewall(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(service.subprocess, "run", _fake_systemctl("firewalld"))
    monkeypatch.setitem(service._FIREWALL_CACHE, "at", 0.0)
    assert "firewall-cmd --permanent --add-port=8790/tcp" in service.firewall_hint(8790, "192.168.1.5")["command"]
    monkeypatch.setattr(service.subprocess, "run", _fake_systemctl("none"))
    monkeypatch.setitem(service._FIREWALL_CACHE, "at", 0.0)
    assert service.firewall_hint(8790, "192.168.1.5") is None
