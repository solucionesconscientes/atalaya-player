"""H44/C6 · entrar en una sala desde Atalaya Player (y no desde el navegador).

Lo que se comprueba aquí:

- el enlace de invitación se entiende (y lo que no es un enlace de sala no se confunde con uno);
- la corrección de deriva del invitado da **exactamente** lo mismo que la de la página (`www/sync.js`): si los dos
  extremos se separan, dos personas en la misma sala ven cosas distintas;
- de punta a punta con dos mpv de verdad: el anfitrión abre la sala, el segundo entra con `share.join`, **carga el
  fichero original** (no el relay: calidad original y cero CPU del anfitrión) y sigue la pausa y la posición del
  anfitrión; al salir, se le avisa a la sala y la velocidad vuelve a 1.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mpvd.rpc import RpcError
from mpvd.share import guest as G
from tests.conftest import ROOT, start_mpv
from tests.test_share_http import MU_OPTS, clip, share_env, _share  # noqa: F401 - fixtures


# -- el enlace -------------------------------------------------------------------------------------------------

def test_el_enlace_de_invitacion_se_entiende():
    link = G.parse_link("http://192.168.1.40:8791/s/Ab3-xY9z#k=" + "t" * 43)
    assert link is not None
    assert link.room == "Ab3-xY9z" and link.token == "t" * 43 and link.port == 8791
    assert link.public is False and link.tls is False and link.base == "http://192.168.1.40:8791"
    # la credencial del invitado viaja en la query porque mpv no manda cookies
    assert link.url("/s/Ab3-xY9z/file", "c0=ok") == "http://192.168.1.40:8791/s/Ab3-xY9z/file?k=c0%3Dok"
    assert link.url("file") == "http://192.168.1.40:8791/s/Ab3-xY9z/file"

    tls = G.parse_link("https://algo.trycloudflare.com/s/abcd#k=xyz&v=1")
    assert tls is not None and tls.tls and tls.port == 443 and tls.public is True
    assert tls.base == "https://algo.trycloudflare.com"   # el :443 no se escribe


@pytest.mark.parametrize("raw", [
    "https://www.youtube.com/watch?v=abc",          # un vídeo, no una sala
    "http://192.168.1.40:8791/s/Ab3-xY9z",          # sin token: el enlace está cortado
    "http://192.168.1.40:8791/x/Ab3-xY9z#k=tok",    # otra ruta
    "http://192.168.1.40:8791/s/no#k=tok",          # id de sala imposible
    "/home/pc/peli.mkv", "", "   ", None,
])
def test_lo_que_no_es_una_sala_no_se_confunde_con_una(raw):
    assert G.parse_link(raw) is None and G.looks_like_room(raw or "") is False


# -- la deriva: la misma cuenta que hace la página ---------------------------------------------------------------

CASES = [(12, 9, 1, False), (12, 11.7, 1, False), (12, 12.4, 1.5, False), (12, 11.95, 1, False),
         (12, 10.6, 1, False), (12, 11, 1, True), (12, 11.9, 1, True), (12, 12, 1, False)]


def test_la_correccion_del_invitado_es_la_de_la_pagina():
    """Si estos dos números se separan, el que entra desde el reproductor y el que entra desde el navegador ven
    cosas distintas en la misma sala. Se comprueba contra sync.js de verdad (con node); sin node, contra los
    valores que sync.js daba, para que el test siga diciendo algo."""
    mine = [G.correction(e, c, s, p) for e, c, s, p in CASES]
    assert [m["action"] for m in mine] == ["seek", "rate", "rate", "none", "rate", "seek", "none", "none"]

    node = shutil.which("node")
    if not node:
        pytest.skip("sin node: no se puede comparar con sync.js")
    script = ("var S = require(process.argv[1]);"
              "console.log(JSON.stringify(" + json.dumps(CASES).replace('true', 'true').replace('false', 'false') +
              ".map(function (c) { return S.correction(c[0], c[1], c[2], !!c[3]); })));")
    out = subprocess.run([node, "-e", script, str(ROOT / "mpvd" / "share" / "www" / "sync.js")],
                         capture_output=True, text=True, check=True).stdout
    theirs = json.loads(out)
    assert len(theirs) == len(mine)
    for js, py, case in zip(theirs, mine, CASES, strict=True):
        assert js["action"] == py["action"], case
        assert abs(js["rate"] - py["rate"]) < 1e-9, case
        assert abs(js["diff"] - py["diff"]) < 1e-9, case
        if js["action"] == "seek":
            assert abs(js["to"] - py["to"]) < 1e-9, case
    assert (G.HARD, G.SOFT, G.MAX_ADJ, G.PAUSED_TOL) == (1.5, 0.15, 0.08, 0.3)


def test_donde_va_el_anfitrion_ahora():
    """`expected` adelanta el reloj mientras el anfitrión reproduce, no mientras está en pausa, y no se pasa del
    final de la película."""
    playing = {"pos": 10.0, "paused": False, "speed": 2.0, "duration": 100.0}
    assert G.expected(playing, 100.0, 100.0) == 10.0
    assert G.expected(playing, 100.0, 103.0) == 16.0          # 3 s a velocidad 2
    assert G.expected({**playing, "paused": True}, 100.0, 103.0) == 10.0
    assert G.expected({**playing, "pos": 99.0}, 100.0, 110.0) == 100.0   # tope en la duración
    assert G.expected(None, 0.0, 1.0) is None


# -- de punta a punta ------------------------------------------------------------------------------------------

def _guest_of(d, timeout: float = 15.0):  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        g = d.call("share.status").get("guest_of")
        if g:
            return g
        time.sleep(0.2)
    raise AssertionError("el invitado no aparece en share.status")


def test_un_segundo_reproductor_entra_en_la_sala_y_sigue_al_anfitrion(share_env, clip):
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    url = d.call("share.create", {"ttl_hours": 1})["url"]
    room = url.split("/s/", 1)[1].split("#")[0]

    g = start_mpv(d.runtime_dir, [MU_OPTS, "--keep-open=yes", "--idle=yes"], env=d.env)
    try:
        g.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        sid = next(s["id"] for s in d.call("sessions.list") if s["ipc"] == str(g.socket))

        with pytest.raises(RpcError):
            d.call("share.join", {"url": "https://www.youtube.com/watch?v=x", "session": sid})
        with pytest.raises(RpcError):          # la sala es de este mismo equipo, pero el token está mal
            d.call("share.join", {"url": url.split("#")[0] + "#k=" + "z" * 43, "session": sid})

        res = d.call("share.join", {"url": url, "name": "Salón", "session": sid})
        assert res["joined"] is True and res["guest"]["name"] == "Salón"
        # el anfitrión se entera, como con cualquier otro invitado
        _share(h, lambda v: v.get("last_notice") == "Salón se ha unido")

        # H44/C6 · el invitado carga el FICHERO ORIGINAL con su credencial, no el relay del navegador
        path = g.wait_property("path", lambda v: isinstance(v, str) and f"/s/{room}/file" in v, timeout=40)
        assert "k=" in path and "v=" in path and path.startswith("http://127.0.0.1:")
        g.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=40)
        assert d.call("share.status")["guest_of"]["kind"] == "file"

        # sigue la pausa del anfitrión en los dos sentidos
        h.command("set", "pause", "no")
        g.wait_property("pause", lambda v: v is False, timeout=20)
        h.command("set", "pause", "yes")
        g.wait_property("pause", lambda v: v is True, timeout=20)

        # y su posición: el anfitrión salta al minuto, el invitado va detrás
        h.command("seek", "25", "absolute+exact")
        g.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 25) < 2.0, timeout=25)
        assert _guest_of(d)["seeks"] >= 1

        # H51 · y si el anfitrión cambia de película, el invitado cambia con él (la dirección del archivo ya no
        # es la misma cadena para todas)
        antes = g.get("path")
        h.command("loadfile", str(ROOT / "tests" / "fixtures" / "media" / "video30.mkv"))
        h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=20)
        g.wait_property("path", lambda v: isinstance(v, str) and v != antes and "/s/" in v, timeout=60)
        assert d.call("share.status")["guest_of"]["title"] != ""

        out = d.call("share.leave")
        assert out["joined"] is False and out["left"] == room
        assert d.call("share.status")["guest_of"] is None
        _share(h, lambda v: v.get("last_notice") == "Salón se ha ido")
        assert g.get("speed") == pytest.approx(1.0, abs=0.001)
    finally:
        g.stop()


# -- el enlace que llega desde fuera -----------------------------------------------------------------------------

def _launcher_args(tmp_path: Path, *args: str) -> list[str]:
    fake = tmp_path / "fake-mpv"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    fake.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MPV_UOS_MPV": str(fake),
           "MPV_UOS_RUNTIME_DIR": str(tmp_path), "MPV_UOS_DATA_DIR": str(tmp_path / "data")}
    out = subprocess.run([str(ROOT / "bin" / "mpv-uos"), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.splitlines()


def test_el_lanzador_no_le_da_el_enlace_de_una_sala_a_mpv(tmp_path):
    """Si el enlace llegara a mpv, yt-dlp intentaría descargarse una página web. Se saca de la lista de cosas que
    reproducir y se le pasa a mu-share, que es quien sabe entrar."""
    link = "http://192.168.1.40:8791/s/Ab3-xY9z#k=" + "t" * 43
    args = _launcher_args(tmp_path, link)
    assert f"--script-opts-append=mu-share-join={link}" in args
    assert link not in args                       # no está entre los ficheros
    assert "--idle=yes" in args

    # un enlace normal sigue yendo a mpv tal cual
    normal = _launcher_args(tmp_path, "https://www.youtube.com/watch?v=abc")
    assert "https://www.youtube.com/watch?v=abc" in normal
    assert not any(a.startswith("--script-opts-append=mu-share-join=") for a in normal)

    # y una película junto al enlace: la película se reproduce y en la sala se entra
    both = _launcher_args(tmp_path, "peli.mkv", link)
    assert "peli.mkv" in both and f"--script-opts-append=mu-share-join={link}" in both


def test_la_puerta_unica_reconoce_una_invitacion():
    """H42 · el enlace llega pegado como cualquier otro, así que la caja de «Abrir o descargar» tiene que saber que
    eso no se reproduce ni se descarga: se entra."""
    lua = (ROOT / "mpv-config" / "scripts" / "mu-ytdl" / "main.lua").read_text(encoding="utf-8")
    assert "kind = 'room'" in lua and "Entrar en esa sala" in lua
    assert "'script-message-to', 'mu_share', 'mu-share-join'" in lua
    share = (ROOT / "mpv-config" / "scripts" / "mu-share" / "main.lua").read_text(encoding="utf-8")
    assert "mp.register_script_message('mu-share-join'" in share


# -- el menú ------------------------------------------------------------------------------------------------------

def test_desde_el_menu_se_entra_y_se_sale_de_la_sala(share_env, clip):
    """El camino que hará Ser: *Compartir → Entrar en una sala de otro… → pegar el enlace*, y la raíz pasa a contar
    en qué sala estás y a ofrecer salir."""
    from tests.conftest import APP
    from tests.test_mu_share import ev, share, titles
    from tests.test_nav import wait_nav

    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    url = d.call("share.create", {"ttl_hours": 1})["url"]

    g = start_mpv(d.runtime_dir, [MU_OPTS, "--keep-open=yes", "--idle=yes"], env=d.env)
    try:
        g.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        g.command("script-binding", "mu_share/share-menu")
        wait_nav(g, "mu-share", f"{APP} › Compartir")
        v = share(g, lambda v: "Entrar en una sala de otro…" in titles(v))
        fila = titles(v).index("Entrar en una sala de otro…") + 1

        ev(g, {"type": "activate", "index": fila, "value": {"view": "join"}})
        # ojo: el estado publica la vista ANTES que sus filas, así que se espera por la fila
        v = share(g, lambda v: v["view"] == "join" and "Escribir o pegar el enlace…" in titles(v))

        fila = titles(v).index("Escribir o pegar el enlace…") + 1
        ev(g, {"type": "activate", "index": fila, "value": {"action": "join-write"}})
        share(g, lambda v: v.get("input") == "join")
        # la caja de texto es una paleta de uosc: el enlace llega como la consulta escrita
        ev(g, {"type": "activate", "index": 1, "value": {"save": url}}, message="mu-share-input-event")

        _share(h, lambda v: (v.get("last_notice") or "").endswith("se ha unido"))
        g.wait_property("path", lambda v: isinstance(v, str) and "/s/" in v and "k=" in v, timeout=40)

        g.command("script-binding", "mu_share/share-menu")
        v = share(g, lambda v: "Salir de la sala" in titles(v))
        assert any(t.startswith("Estás en la sala") for t in titles(v))
        assert v["guest_of"]["connected"] is True and v["guest_of"]["kind"] == "file"
        fila = titles(v).index("Salir de la sala") + 1
        ev(g, {"type": "activate", "index": fila, "value": {"action": "leave"}})
        share(g, lambda v: "Entrar en una sala de otro…" in titles(v) and v["guest_of"]["room"] == "",
              timeout=25)
        assert d.call("share.status")["guest_of"] is None
    finally:
        g.stop()
