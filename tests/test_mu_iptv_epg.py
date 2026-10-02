"""mu-iptv with the TV guide and scheduled recordings (H21), headless mpv + daemon + local HTTP server: «ahora» in
the channel lists (by tvg-id and by name), the guide of a channel, «Grabar este programa», the list of scheduled
recordings (cancel), the manual time palette and the OSD when a recording made by mpvd ends.
H43: «Programar una grabación…» en el primer nivel (y en el menú de Grabar) y la radio también se programa."""

from __future__ import annotations

import gzip
import json
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import APP, start_mpv
from tests.test_mu_iptv import free_port, send_event, serve, start_live_stream, wait_view


def xmltv_time(ts: float) -> str:
    return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(ts))


def guide_xml(t0: float) -> bytes:
    progs = [("Uno.TV", -600, 3000, "Noticias Uno"), ("Uno.TV", 3000, 6600, "Película Uno"),
             ("Uno.TV", 6600, 90000, "Madrugada Uno"),
             ("CanalDos.TV", -1200, 2400, "Deportes Dos"), ("CanalDos.TV", 2400, 6000, "Concurso Dos")]
    body = ["<?xml version='1.0' encoding='utf-8'?>\n<tv>\n",
            '\t<channel id="Uno.TV">\n\t\t<display-name>Uno.TV</display-name>\n\t</channel>\n',
            '\t<channel id="CanalDos.TV">\n\t\t<display-name>CanalDos.TV</display-name>\n\t</channel>\n']
    for ch, a, b, title in progs:
        body.append(f'\t<programme channel="{ch}" start="{xmltv_time(t0 + a)}" stop="{xmltv_time(t0 + b)}">\n'
                    f"\t\t<title>{title}</title>\n\t\t<desc>Descripción de {title}.</desc>\n\t</programme>\n")
    body.append("</tv>\n")
    return gzip.compress("".join(body).encode("utf-8"))


@pytest.fixture
def tv(daemon_env, media_dir):
    m = media_dir
    live_port = free_port()
    live = start_live_stream(live_port)
    t0 = float(int(time.time()))
    files: dict[str, bytes] = {"/epg.xml.gz": guide_xml(t0)}
    httpd = serve(files)
    base = f"http://127.0.0.1:{httpd.server_port}"
    files["/tv.m3u8"] = (
        f'#EXTM3U url-tvg="{base}/epg.xml.gz"\n'
        f'#EXTINF:-1 tvg-id="Uno.TV" group-title="Pruebas",Canal Uno\nfile://{m}/video30.mkv\n'
        f'#EXTINF:-1 group-title="Pruebas",Canal Dos\nfile://{m}/chapters.mkv\n'
        f'#EXTINF:-1 tvg-id="Live.TV" group-title="Directo",Directo Test\nhttp://127.0.0.1:{live_port}/live.ts\n'
    ).encode()
    files["/radio.m3u8"] = (
        f'#EXTM3U\n#EXTINF:-1 radio="true" group-title="Radio_Pruebas",Emisora Voz\nfile://{m}/voz_es.flac\n'
    ).encode()
    files["/world.m3u"] = b"#EXTM3U\n"
    files["/json/stations/search"] = b"[]"
    sources = [
        {"id": "tdt_tv", "name": "España TV", "url": f"{base}/tv.m3u8", "kind": "tv", "region": "es", "country": "es"},
        {"id": "tdt_radio", "name": "España Radio", "url": f"{base}/radio.m3u8", "kind": "radio", "region": "es"},
        {"id": "iptv_org", "name": "Mundo", "url": f"{base}/world.m3u", "kind": "tv", "region": "world"},
    ]
    src_path = daemon_env.base / "sources.json"
    src_path.write_text(json.dumps(sources), encoding="utf-8")
    rec_dir = daemon_env.base / "Grabaciones"
    # H40: ninguna orden de energía real en los tests; el programa falso solo apunta lo que se le pidió
    fake_power = daemon_env.base / "fake-power"
    fake_power.write_text("#!/usr/bin/env python3\nimport sys, pathlib\n"
                          "pathlib.Path(sys.argv[0] + '.log').open('a').write(' '.join(sys.argv[1:]) + '\\n')\n",
                          encoding="utf-8")
    fake_power.chmod(0o755)
    env = {**daemon_env.env, "MPV_UOS_IPTV_SOURCES": str(src_path), "MPV_UOS_COUNTRY": "es",
           "MPV_UOS_RADIO_BROWSER_URL": base, "MPVD_POWER_FAKE": str(fake_power), "MPV_UOS_NO_NOTIFY": "1"}
    h = start_mpv(daemon_env.runtime_dir,
                  [f"--script-opts=mu-core-watchdog_seconds=5,mu-iptv-osd_seconds=1,mu-iptv-schedule_dir={rec_dir}"],
                  env=env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env, rec_dir, t0, fake_power
    finally:
        h.stop()
        httpd.shutdown()
        live.kill()
        live.wait(timeout=10)


def rows(menu: dict) -> list[dict]:
    """Rows of the published menu, submenus flattened (two levels)."""
    out = []
    for r in menu.get("items") or []:
        out.append(r)
        out.extend(r.get("items") or [])
    return out


def wait_menu(h, pred, timeout: float = 30.0) -> dict:
    return h.wait_property("user-data/mu/iptv-menu", lambda v: bool(v) and pred(v), timeout=timeout)


def schedule(d) -> list[dict]:
    return d.call("iptv.schedule.list")["items"]


def test_now_hints_guide_and_schedule_from_the_guide(tv):
    h, d, rec_dir, t0, _fake = tv
    chans = {c["name"]: c["id"] for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}

    # «ahora» in the list: Canal Uno by its tvg-id, Canal Dos by its name (no tvg-id); the first query finds the
    # guide still downloading and mu-iptv asks again by itself
    h.command("script-binding", "mu_iptv/tv-menu")
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "source", "id": "tdt_tv"}})

    def hints_ready(v):
        hints = {r["title"]: r["hint"] for r in rows(v)}
        return "ahora: Noticias Uno" in hints.get("Canal Uno", "") and "ahora: Deportes Dos" in hints.get("Canal Dos", "")

    menu = wait_menu(h, hints_ready, 40)
    hints = {r["title"]: r["hint"] for r in rows(menu)}
    assert "ahora" not in hints["Directo Test"]  # not in the guide
    assert h.get("user-data/mu/iptv")["epg_hints"] >= 2

    # Tab › Guía on Canal Uno: now, next and the rest, each with its actions
    send_event(h, {"type": "activate", "index": 1, "action": "guide", "value": {"play": chans["Canal Uno"],
                                                                                 "name": "Canal Uno"}})
    wait_view(h, "guide:" + chans["Canal Uno"])
    guide = wait_menu(h, lambda v: v.get("title") == "Guía · Canal Uno" and len(v["items"]) >= 3)
    # la guía intercala una fila de día («Mañana, sábado 3») cuando un programa cae pasada medianoche, y eso
    # pasa o no según la hora a la que se ejecute el test: se quitan las filas que no son programas
    top = [r for r in guide["items"] if r.get("items") or (r.get("value") or {}).get("play")]
    assert top[0]["title"].endswith("Noticias Uno") and top[0]["hint"].startswith("ahora · quedan")
    assert top[1]["title"].endswith("Película Uno") and top[1]["hint"] == "después"
    subs = {r["title"]: r for r in top[1]["items"]}
    assert "Descripción de Película Uno." in subs and "Ver el canal ahora" in subs
    rec_value = subs["Grabar este programa"]["value"]
    assert "Grabar lo que queda" in {r["title"] for r in top[0]["items"]}
    assert h.get("user-data/mu/iptv")["guide"]["programmes"] == 3

    # «Grabar este programa»: scheduled in mpvd with the margins of mu-iptv, marked ⏺ in the guide
    send_event(h, {"type": "activate", "index": 1, "value": rec_value})
    d.wait(lambda: any(r["title"] == "Película Uno" for r in schedule(d)), timeout=15)
    rec = next(r for r in schedule(d) if r["title"] == "Película Uno")
    assert rec["status"] == "scheduled" and rec["origin"] == "epg"
    assert (rec["start"], rec["stop"]) == (t0 + 3000, t0 + 6600)
    assert (rec["margin_before"], rec["margin_after"]) == (60, 180) and rec["dir"] == str(rec_dir)
    wait_menu(h, lambda v: v.get("title") == "Guía · Canal Uno"
              and any(r["title"].endswith("Película Uno") and r["hint"].startswith("⏺") for r in v["items"]))

    # «Grabaciones programadas»: the new one, and «Cancelar grabación» works
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "schedule"}})
    wait_view(h, "schedule")
    sched = wait_menu(h, lambda v: v.get("title") == "Grabaciones programadas"
                      and any(r["title"] == "Canal Uno · Película Uno" for r in v["items"]))
    row = next(r for r in sched["items"] if r["title"] == "Canal Uno · Película Uno")
    assert row["hint"].endswith("programada")
    cancel = next(r for r in row["items"] if r["title"] == "Cancelar grabación")
    send_event(h, {"type": "activate", "index": 1, "value": cancel["value"]})
    d.wait(lambda: next(r for r in schedule(d) if r["id"] == rec["id"])["status"] == "cancelled", timeout=15)
    wait_menu(h, lambda v: v.get("title") == "Grabaciones programadas"
              and any(r["title"] == "Canal Uno · Película Uno" and r["hint"].endswith("cancelada") for r in v["items"]))
    assert not h.script_errors(), h.script_errors()


def test_manual_schedule_palette_and_osd_when_a_recording_ends(tv):
    h, d, rec_dir, _, _fake = tv
    chans = {c["name"]: c["id"] for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}

    # «Programar grabación…» by hand: channel, then «mañana 21:30 22:15» in the palette
    h.command("script-binding", "mu_iptv/tv-schedule")
    wait_view(h, "schedule")
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "sched_new"}})
    wait_view(h, "sched_new")
    send_event(h, {"type": "activate", "index": 2, "action": "schedule",
                   "value": {"play": chans["Canal Dos"], "name": "Canal Dos"}})
    wait_view(h, "sched_time:" + chans["Canal Dos"])
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv", timeout=10)
    send_event(h, {"type": "search", "query": "mañana 21:30"})
    menu = wait_menu(h, lambda v: v.get("title") == "sched_time" and v["items"][0]["title"].startswith("Grabar"))
    assert menu["items"][0]["title"].startswith("Grabar «Canal Dos» mañana 21:30–22:30")  # one hour by default
    send_event(h, {"type": "search", "query": "mañana 21:30 22:15"})
    menu = wait_menu(h, lambda v: v.get("title") == "sched_time" and "22:15" in v["items"][0]["title"])
    assert menu["items"][0]["title"] == "Grabar «Canal Dos» mañana 21:30–22:15 (45 min)"
    send_event(h, {"type": "search", "query": "a las nueve"})
    menu = wait_menu(h, lambda v: v.get("title") == "sched_time" and not v["items"][0]["title"].startswith("Grabar"))
    assert "hora" in menu["items"][0]["title"]
    send_event(h, {"type": "search", "query": "mañana 21:30 22:15"})
    menu = wait_menu(h, lambda v: v.get("title") == "sched_time" and "22:15" in v["items"][0]["title"])
    send_event(h, {"type": "activate", "index": 1, "value": menu["items"][0]["value"]})
    wait_view(h, "schedule")
    d.wait(lambda: any(r["channel"]["name"] == "Canal Dos" for r in schedule(d)), timeout=15)
    rec = next(r for r in schedule(d) if r["channel"]["name"] == "Canal Dos")
    tomorrow = time.localtime(time.time() + 86400)
    start = time.localtime(rec["start"])
    assert (start.tm_mday, start.tm_hour, start.tm_min) == (tomorrow.tm_mday, 21, 30)
    assert rec["stop"] - rec["start"] == 45 * 60 and rec["origin"] == "manual" and rec["margin_before"] == 0

    # a short recording made by mpvd while the player shows something else: OSD event at start and end
    h.command("loadfile", str(Path(h.get("working-directory")) / "tests/fixtures/media/video30.mkv"))
    now = time.time()
    short = d.call("iptv.schedule.add", {"channel": chans["Directo Test"], "start": now + 1, "stop": now + 4,
                                         "dir": str(rec_dir)})
    h.wait_property("user-data/mu/iptv", lambda v: v["schedule_event"] and v["schedule_event"]["id"] == short["id"]
                    and v["schedule_event"]["status"] == "recording", timeout=15)
    ev = h.wait_property("user-data/mu/iptv", lambda v: v["schedule_event"]["id"] == short["id"]
                         and v["schedule_event"]["status"] in ("done", "failed"), timeout=30)["schedule_event"]
    assert ev["status"] == "done" and ev["text"] == "✔ Grabación terminada: Directo Test", ev
    out = Path(ev["file"])
    assert out.parent == rec_dir and out.suffix == ".mkv"
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json",
                                         str(out)], capture_output=True, text=True, check=True).stdout)["streams"]
    assert sorted(s["codec_type"] for s in streams) == ["audio", "video"]
    assert not h.script_errors(), h.script_errors()


def test_despertar_antes_y_apagar_al_terminar(tv):
    """H40/F1: las dos opciones viven en mpvd (valen con el reproductor cerrado) y se heredan en lo que se programe."""
    h, d, rec_dir, _t0, fake = tv
    chans = {c["name"]: c["id"] for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}

    # de serie, nada: no se toca la energía de nadie sin pedirlo
    assert d.call("iptv.schedule.defaults")["wake"] is False
    assert d.call("iptv.schedule.defaults")["after"] == "nothing"

    h.command("script-binding", "mu_iptv/tv-schedule")
    wait_view(h, "schedule")
    menu = wait_menu(h, lambda v: any(r["title"] == "Despertar el equipo 5 min antes" for r in rows(v)))
    titulos = [r["title"] for r in rows(menu)]
    assert "Al terminar la grabación" in titulos
    despertar = next(r for r in rows(menu) if r["title"] == "Despertar el equipo 5 min antes")
    al_terminar = next(r for r in rows(menu) if r["title"] == "Al terminar la grabación")
    assert despertar["hint"].startswith("no") and al_terminar["hint"].startswith("nada")

    # encender el despertador y pasar «al terminar» a suspender
    send_event(h, {"type": "activate", "index": 1, "value": despertar["value"]})
    d.wait(lambda: d.call("iptv.schedule.defaults")["wake"] is True, timeout=15)
    send_event(h, {"type": "activate", "index": 1, "value": al_terminar["value"]})
    d.wait(lambda: d.call("iptv.schedule.defaults")["after"] == "suspend", timeout=15)
    menu = wait_menu(h, lambda v: any(r["title"] == "Al terminar la grabación" and "suspender" in (r["hint"] or "")
                                      for r in rows(v)))

    # lo que se programe a partir de ahora lo hereda, y el despertador se pone 5 min antes
    start = time.time() + 3600
    rec = d.call("iptv.schedule.add", {"channel": chans["Canal Dos"], "start": start, "stop": start + 600})
    assert rec["wake"] is True and rec["after"] == "suspend"
    log = Path(str(fake) + ".log")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not log.exists():
        time.sleep(0.2)
    assert log.exists(), "la grabación con despertador tiene que poner la alarma"
    assert str(int(rec["begin"] - 300)) in log.read_text(encoding="utf-8")

    # y «al terminar» vuelve a nada dando dos vueltas más (nada → suspender → apagar → nada)
    send_event(h, {"type": "activate", "index": 1, "value": al_terminar["value"]})
    d.wait(lambda: d.call("iptv.schedule.defaults")["after"] == "shutdown", timeout=15)
    send_event(h, {"type": "activate", "index": 1, "value": al_terminar["value"]})
    d.wait(lambda: d.call("iptv.schedule.defaults")["after"] == "nothing", timeout=15)
    assert h.script_errors() == [], h.script_errors()


def test_programar_visible_y_la_radio_tambien_se_programa(tv):
    """H43/B2-B3: «Programar una grabación…» está en el primer nivel de «TV y radio» y también en el menú de Grabar,
    y una emisora de radio se puede programar: mpvd ya la graba (.mka), era la interfaz la que lo prohibía."""
    h, d, rec_dir, _t0, _fake = tv
    radio = d.call("iptv.channels", {"source": "tdt_radio", "compact": True})["items"][0]
    assert radio["kind"] == "radio"
    d.call("iptv.favorites.toggle", {"id": radio["id"]})   # para que salga en el selector de canal

    # B2 · en el primer nivel, no solo con Tab dentro de la lista de un canal
    h.command("script-binding", "mu_iptv/tv-menu")
    wait_view(h, "root")
    menu = wait_menu(h, lambda v: any(r["title"] == "Programar una grabación…" for r in rows(v)))
    fila = next(r for r in rows(menu) if r["title"] == "Programar una grabación…")
    assert fila["value"] == {"view": "sched_new"}
    send_event(h, {"type": "activate", "index": 1, "value": fila["value"]})
    wait_view(h, "sched_new")

    # B3 · la radio aparece en el selector (antes la excluía `ch.kind ~= 'radio'`) y se programa de verdad
    menu = wait_menu(h, lambda v: any(r["title"] == radio["name"] for r in rows(v)))
    fila = next(r for r in rows(menu) if r["title"] == radio["name"])
    send_event(h, {"type": "activate", "index": 1, "value": fila["value"]})
    wait_view(h, "sched_time:" + radio["id"])
    send_event(h, {"type": "search", "query": "mañana 7:00 7:30"})
    menu = wait_menu(h, lambda v: v.get("title") == "sched_time" and v["items"][0]["title"].startswith("Grabar"))
    send_event(h, {"type": "activate", "index": 1, "value": menu["items"][0]["value"]})
    wait_view(h, "schedule")
    d.wait(lambda: any(r["channel"]["id"] == radio["id"] for r in schedule(d)), timeout=15)

    # y mpvd la graba como audio: una programada corta termina en .mka
    now = time.time()
    short = d.call("iptv.schedule.add", {"channel": radio["id"], "start": now + 1, "stop": now + 4,
                                         "dir": str(rec_dir)})
    ev = h.wait_property("user-data/mu/iptv", lambda v: v["schedule_event"] and v["schedule_event"]["id"] == short["id"]
                         and v["schedule_event"]["status"] in ("done", "failed"), timeout=40)["schedule_event"]
    assert ev["status"] == "done", ev
    out = Path(ev["file"])
    assert out.suffix == ".mka" and out.parent == rec_dir
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json",
                                         str(out)], capture_output=True, text=True, check=True).stdout)["streams"]
    assert sorted(s["codec_type"] for s in streams) == ["audio"]

    # B2 · y desde «Grabar»: la fila abre mu-iptv como hija, con las migas del sitio de donde viene
    h.command("script-binding", "mu_record/record-menu")
    st = h.wait_property("user-data/mu/record", lambda v: bool(v) and v.get("view") == "root" and v.get("items"),
                         timeout=20)
    fila = next(i for i in st["items"] if i["title"] == "Programar una grabación…")
    assert fila["value"] == {"child": {"script": "mu_iptv", "entry": "tv-schedule-new"}}
    h.command("script-message-to", "mu_record", "mu-record-event", json.dumps(
        {"type": "activate", "index": 1, "menu_id": "{root}", "value": fila["value"]}))
    nav = h.wait_property("user-data/mu/nav", lambda v: bool(v) and v.get("script") == "mu_iptv", timeout=20)
    assert nav["title"].startswith(f"{APP} › Grabar"), nav
    wait_view(h, "sched_new")
    assert not h.script_errors(), h.script_errors()
