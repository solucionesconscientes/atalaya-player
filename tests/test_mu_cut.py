"""H52 · tramos y marcas en la línea de tiempo.

Lo que se comprueba:

- marcar «desde aquí / hasta aquí» deja el tramo pintado en la línea de tiempo **sin perder los capítulos de la
  película**: `chapter-list` tiene un solo dueño (mu-marks) justo para esto;
- el bucle repite el tramo elegido;
- guardar los tramos **unidos** produce un archivo con la duración de la suma, no la del original;
- una nota aparece como marca en la línea de tiempo.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import ROOT, start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


@pytest.fixture
def cut_mpv(daemon_env, media_dir):
    daemon_env.extra_env.update({"MPV_UOS_VAAPI": "0", "MPV_UOS_YTDLP_AUTO_UPDATE": "0"})
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes",
                                           str(media_dir / "chapters.mkv")], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 10, timeout=20)
        yield h, daemon_env
    finally:
        h.stop()


def cut_state(h, pred, timeout: float = 15.0) -> dict:
    return h.wait_property("user-data/mu/cut", lambda v: bool(v) and pred(v), timeout=timeout)


def chapters(h) -> list[tuple[float, str]]:
    return [(round(c["time"], 2), c["title"]) for c in (h.get("chapter-list") or [])]


def mark_at(h, seconds: float) -> None:
    h.command("set_property", "time-pos", seconds)
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - seconds) < 1.5, timeout=10)
    h.command("script-binding", "mu_cut/cut-mark")


def test_un_tramo_se_pinta_en_la_linea_de_tiempo_sin_perder_los_capitulos(cut_mpv):
    h, _d = cut_mpv
    originales = chapters(h)
    assert [t for _, t in originales] == ["Capítulo uno", "Capítulo dos", "Capítulo tres"], originales

    mark_at(h, 3.0)
    cut_state(h, lambda v: v["pending"] >= 0)
    assert h.get("ab-loop-a") == 3.0          # uosc dibuja la A él solo mientras eliges

    mark_at(h, 9.0)
    v = cut_state(h, lambda v: v["count"] == 1)
    assert v["segments"][0]["a"] == 3.0 and v["segments"][0]["b"] == 9.0
    assert v["pending"] == -1 and h.get("ab-loop-a") == "no"

    marcas = chapters(h)
    # el tramo, de su marca de inicio a la de fin (es lo que uosc colorea), y los capítulos de la peli INTACTOS
    assert (3.0, "tramo 1 · 0:03-0:09") in marcas
    assert (9.0, "fin del tramo 1") in marcas
    titulos = [t for _, t in marcas]
    # los tres capítulos de la película siguen ahí (el primero empieza en -0,02 s y mpv descarta los tiempos
    # negativos al escribir la lista: por eso mu-marks los lleva a 0)
    assert [t for t in titulos if t.startswith("Capítulo")] == [t for _, t in originales]
    assert len(marcas) == len(originales) + 2
    assert marcas == sorted(marcas, key=lambda x: x[0])      # van en orden de tiempo, mezcladas con las de la peli

    # un segundo tramo se numera y se ordena por tiempo
    mark_at(h, 22.0)
    mark_at(h, 25.0)
    v = cut_state(h, lambda v: v["count"] == 2)
    assert [(s["a"], s["b"]) for s in v["segments"]] == [(3.0, 9.0), (22.0, 25.0)]
    assert any(t.startswith("tramo 2") for _, t in chapters(h))
    assert h.script_errors() == [], h.script_errors()


def test_el_bucle_repite_el_tramo_elegido(cut_mpv):
    h, _d = cut_mpv
    mark_at(h, 4.0)
    mark_at(h, 8.0)
    cut_state(h, lambda v: v["count"] == 1)
    h.command("script-binding", "mu_cut/cut-loop")
    h.wait_property("ab-loop-b", lambda v: v == 8.0, timeout=10)
    assert h.get("ab-loop-a") == 4.0
    h.command("script-binding", "mu_cut/cut-loop")      # y deja de repetir
    h.wait_property("ab-loop-b", lambda v: v == "no", timeout=10)
    assert h.script_errors() == [], h.script_errors()


def test_al_cambiar_de_pelicula_los_tramos_se_van(cut_mpv, media_dir):
    h, _d = cut_mpv
    mark_at(h, 3.0)
    mark_at(h, 9.0)
    cut_state(h, lambda v: v["count"] == 1)
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=20)
    cut_state(h, lambda v: v["count"] == 0, timeout=20)
    assert not any("tramo" in t for _, t in chapters(h))


def test_guardar_los_tramos_unidos_da_un_archivo_con_la_suma(cut_mpv, media_dir, tmp_path):
    """«juntar los que quieras»: tres trozos de 3+3+4 s tienen que dar 10 s, no los 30 del original."""
    _h, d = cut_mpv
    out = tmp_path / "salida"
    res = d.call("convert.cut", {
        "path": str(media_dir / "video30.mkv"),
        "segments": [{"start": 2, "end": 5}, {"start": 10, "end": 13}, {"start": 20, "end": 24}],
        "preset": "mp4", "joined": True, "options": {"speed": "fast"}, "out_dir": str(out),
    }, timeout=60)
    assert res["count"] == 1
    item = res["items"][0]
    fin = time.monotonic() + 180
    while time.monotonic() < fin:
        got = d.call("convert.get", {"id": item["id"]})
        if got["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.5)
    assert got["status"] == "done", got
    hecho = Path(got["output"])
    assert hecho.name == "video30 [3 tramos].mp4", hecho.name
    dur = float(json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(hecho)],
        capture_output=True, text=True, check=True).stdout)["format"]["duration"])
    assert abs(dur - 10.0) < 0.6, f"duración {dur}, esperada 10"


def test_guardar_los_tramos_por_separado_da_un_archivo_cada_uno(cut_mpv, media_dir, tmp_path):
    _h, d = cut_mpv
    out = tmp_path / "sueltos"
    res = d.call("convert.cut", {
        "path": str(media_dir / "video30.mkv"),
        "segments": [{"start": 2, "end": 5}, {"start": 10, "end": 13}],
        "preset": "m4a", "joined": False, "options": {"speed": "fast"}, "out_dir": str(out),
    }, timeout=60)
    assert res["count"] == 2
    fin = time.monotonic() + 180
    hechos = []
    while time.monotonic() < fin:
        hechos = [d.call("convert.get", {"id": i["id"]}) for i in res["items"]]
        if all(x["status"] in ("done", "error", "cancelled") for x in hechos):
            break
        time.sleep(0.5)
    assert [x["status"] for x in hechos] == ["done", "done"], hechos
    nombres = sorted(Path(x["output"]).name for x in hechos)
    assert nombres == ["video30 [00.00.02-00.00.05].m4a", "video30 [00.00.10-00.00.13].m4a"], nombres


def test_una_nota_sale_en_la_linea_de_tiempo(cut_mpv):
    h, d = cut_mpv
    path = h.get("path")
    d.call("notes.add", {"text": "aquí pasa algo", "path": path, "time_pos": 7.0, "title": "chapters"})
    h.command("script-message-to", "mu_notes", "mu-notes-refresh")
    h.wait_property("chapter-list", lambda v: any("nota · aquí pasa algo" in (c.get("title") or "")
                                                  for c in (v or [])), timeout=20)
    # y sigue conviviendo con los capítulos de la película
    assert any(t == "Capítulo dos" for _, t in chapters(h))
    assert h.script_errors() == [], h.script_errors()


def ev_cut(h, event: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_cut", "mu-cut-event", json.dumps({**base, **event}))


def titles_cut(v: dict) -> list[str]:
    return [i["title"] for i in v.get("items") or []]


def elegir_formato(h, v, preset: str, etiqueta: str):
    """Pasa por la fila «Formato» y elige uno, como se hace a mano. Desde H62 el de fábrica es «Sin recodificar»,
    que no puede unir, así que un test sobre unir tiene que pedir antes uno que recodifique."""
    fila = next(i for i, x in enumerate(titles_cut(v), start=1) if x == "Formato")
    ev_cut(h, {"type": "activate", "index": fila, "value": {"view": "format"}})
    v = cut_state(h, lambda s: s["view"] == "format" and any(etiqueta in x for x in titles_cut(s)))
    fila = next(i for i, x in enumerate(titles_cut(v), start=1) if etiqueta in x)
    ev_cut(h, {"type": "activate", "index": fila, "value": {"preset": preset}})
    return cut_state(h, lambda s: s["preset"] == preset and s["view"] == "root")


def test_se_eligen_los_tramos_que_se_exportan(cut_mpv):
    """H54 · «no veo la forma de elegir los que exportas». Cada tramo entra elegido y se deja fuera con Enter
    sobre su fila; lo que se guarda son solo los elegidos, y con uno solo no se ofrece unir."""
    h, _d = cut_mpv
    for a, b in ((3.0, 6.0), (10.0, 13.0), (20.0, 23.0)):
        mark_at(h, a)
        mark_at(h, b)
    v = cut_state(h, lambda v: v["count"] == 3)
    assert v["chosen"] == 3 and all(s["on"] for s in v["segments"])

    h.command("script-binding", "mu_cut/cut-menu")
    v = cut_state(h, lambda v: v["view"] == "root" and any(t.startswith("Guardar los 3 elegidos por separado")
                                                           for t in titles_cut(v)))
    v = elegir_formato(h, v, "mp4", "MP4")   # unir pide un formato que recodifique (H62)
    v = cut_state(h, lambda v: any(t.startswith("Guardar los 3 elegidos unidos") for t in titles_cut(v)))

    # dejar fuera el segundo
    ev_cut(h, {"type": "activate", "index": 1, "value": {"action": "toggle", "index": 2}})
    v = cut_state(h, lambda v: v["chosen"] == 2
                  and any(t.startswith("Guardar los 2 elegidos") for t in titles_cut(v)))
    assert [s["on"] for s in v["segments"]] == [True, False, True]
    # lo elegido suma lo que miden los elegidos, sea cual sea el punto exacto donde cayeron las marcas: bajo carga
    # `mark_at` puede dejarlas un segundo más allá y clavar un número aquí hace el test frágil
    esperado = sum(x["b"] - x["a"] for x in v["segments"] if x["on"])
    assert abs(v["chosen_total"] - esperado) < 0.01
    assert v["chosen_total"] < v["total"]

    # con uno solo elegido no se ofrece unir: no hay nada que unir
    ev_cut(h, {"type": "activate", "index": 1, "value": {"action": "toggle", "index": 3}})
    v = cut_state(h, lambda v: v["chosen"] == 1 and "Guardar el tramo elegido" in titles_cut(v))
    assert not any("unidos" in t for t in titles_cut(v))

    # «elegirlos todos» los devuelve
    fila = next(i for i, t in enumerate(titles_cut(v), start=1) if t == "Elegirlos todos")
    ev_cut(h, {"type": "activate", "index": fila, "value": {"action": "toggle-all"}})
    v = cut_state(h, lambda v: v["chosen"] == 3 and "Dejar fuera todos" in titles_cut(v))

    # y los botones de la derecha de una fila siguen funcionando (llegan como `action`)
    ev_cut(h, {"type": "activate", "index": 1, "action": "drop", "value": {"action": "toggle", "index": 2}})
    v = cut_state(h, lambda v: v["count"] == 2)
    assert [(s["a"], s["b"]) for s in v["segments"]] == [(3.0, 6.0), (20.0, 23.0)]
    assert h.script_errors() == [], h.script_errors()


def test_solo_se_guardan_los_tramos_elegidos(cut_mpv, media_dir, tmp_path):
    """Lo que se exporta es lo elegido, no todo: dos de tres tramos unidos dan la suma de esos dos."""
    _h, d = cut_mpv
    out = tmp_path / "elegidos"
    res = d.call("convert.cut", {
        "path": str(media_dir / "video30.mkv"),
        "segments": [{"start": 2, "end": 5}, {"start": 20, "end": 24}],   # el de en medio se quedó fuera
        "preset": "mp4", "joined": True, "options": {"speed": "fast"}, "out_dir": str(out),
    }, timeout=60)
    item = res["items"][0]
    fin = time.monotonic() + 180
    while time.monotonic() < fin:
        got = d.call("convert.get", {"id": item["id"]})
        if got["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.5)
    assert got["status"] == "done", got
    dur = float(json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", got["output"]],
        capture_output=True, text=True, check=True).stdout)["format"]["duration"])
    assert abs(dur - 7.0) < 0.6, f"duración {dur}, esperada 7 (3 + 4)"


def test_se_cambia_el_orden_y_se_unen_en_ese_orden(cut_mpv):
    """H55 · «cambiar el orden, y juntar 2 o más en el orden deseado». Mientras no toques nada entran por tiempo;
    en cuanto subes uno, el orden es tuyo y es el que se usa al unirlos."""
    h, _d = cut_mpv
    for a, b in ((3.0, 6.0), (10.0, 13.0), (20.0, 23.0)):
        mark_at(h, a)
        mark_at(h, b)
    v = cut_state(h, lambda v: v["count"] == 3)
    assert [s["a"] for s in v["segments"]] == [3.0, 10.0, 20.0] and v["manual"] is False

    h.command("script-binding", "mu_cut/cut-menu")
    cut_state(h, lambda v: v["view"] == "root" and any("Guardar" in t for t in titles_cut(v)))
    # subir el tercero: pasa a ser el segundo y el orden deja de ser por tiempo
    ev_cut(h, {"type": "activate", "index": 1, "action": "up", "value": {"action": "toggle", "index": 3}})
    v = cut_state(h, lambda v: v["manual"] is True)
    assert [s["a"] for s in v["segments"]] == [3.0, 20.0, 10.0]

    # y marcar otro ya NO lo recoloca por tiempo: manda el orden que has puesto
    # (los dos puntos, bien separados: `mark_at` espera con 1,5 s de tolerancia y dos marcas juntas se descartan)
    mark_at(h, 25.0)
    mark_at(h, 28.0)
    v = cut_state(h, lambda v: v["count"] == 4)
    assert [s["a"] for s in v["segments"]] == [3.0, 20.0, 10.0, 25.0]
    assert h.script_errors() == [], h.script_errors()


def test_unidos_respetan_el_orden_elegido(cut_mpv, media_dir, tmp_path):
    """El trozo del final puesto primero tiene que salir primero en el archivo."""
    _h, d = cut_mpv
    out = tmp_path / "orden"
    res = d.call("convert.cut", {
        "path": str(media_dir / "video30.mkv"),
        "segments": [{"start": 20, "end": 24}, {"start": 2, "end": 5}],   # al revés a propósito
        "preset": "mp4", "joined": True, "options": {"speed": "fast"}, "out_dir": str(out),
    }, timeout=60)
    item = res["items"][0]
    fin = time.monotonic() + 180
    while time.monotonic() < fin:
        got = d.call("convert.get", {"id": item["id"]})
        if got["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.5)
    assert got["status"] == "done", got
    dur = float(json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", got["output"]],
        capture_output=True, text=True, check=True).stdout)["format"]["duration"])
    assert abs(dur - 7.0) < 0.6


def test_por_defecto_no_recodifica_y_para_unir_hay_que_elegir_formato(cut_mpv):
    """H62 · Ser lo daba por supuesto y no era así: el formato por defecto era MP4, o sea RECODIFICAR. Ahora es
    «Sin recodificar», que copia los flujos tal cual. Unir varios en uno sí obliga a recodificar —pegar trozos es
    un filtro—, así que con el formato por defecto la lista no ofrece unir: lo explica y señala «Formato»."""
    h, d = cut_mpv
    ids = [p["id"] for p in d.call("convert.presets")["presets"]]
    assert "copy" in ids and "mp4" in ids
    for a, b in ((3.0, 6.0), (10.0, 13.0)):
        mark_at(h, a)
        mark_at(h, b)
    cut_state(h, lambda v: v["count"] == 2)

    h.command("script-binding", "mu_cut/cut-menu")
    # de fábrica: copiar, y por eso no hay fila de unir sino la explicación
    v = cut_state(h, lambda v: v["preset"] == "copy" and v["view"] == "root"
                  and any("Para unirlos en uno hay que recodificar" in t for t in titles_cut(v)))
    assert not any("unidos en uno" in t for t in titles_cut(v))

    # y en cuanto se elige un formato que recodifica, aparece
    fila = next(i for i, t in enumerate(titles_cut(v), start=1) if t == "Formato")
    ev_cut(h, {"type": "activate", "index": fila, "value": {"view": "format"}})
    v = cut_state(h, lambda v: v["view"] == "format" and any("MP4" in t for t in titles_cut(v)))
    fila = next(i for i, t in enumerate(titles_cut(v), start=1) if "MP4" in t)
    ev_cut(h, {"type": "activate", "index": fila, "value": {"preset": "mp4"}})
    v = cut_state(h, lambda v: v["preset"] == "mp4" and v["view"] == "root"
                  and any(t.startswith("Guardar los 2 elegidos unidos") for t in titles_cut(v)))
    assert h.script_errors() == [], h.script_errors()


def test_sin_recodificar_es_instantaneo_y_no_pierde_calidad(cut_mpv, media_dir, tmp_path):
    """Un corte copiando los flujos conserva los códecs del original (aquí H.264 + AAC) y tarda una fracción."""
    _h, d = cut_mpv
    out = tmp_path / "copia"
    res = d.call("convert.cut", {"path": str(media_dir / "video30.mkv"),
                                 "segments": [{"start": 2, "end": 8}], "preset": "copy", "out_dir": str(out)},
                 timeout=60)
    item = res["items"][0]
    fin = time.monotonic() + 120
    while time.monotonic() < fin:
        got = d.call("convert.get", {"id": item["id"]})
        if got["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.3)
    assert got["status"] == "done", got
    meta = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", got["output"]],
                                     capture_output=True, text=True, check=True).stdout)
    assert {st["codec_name"] for st in meta["streams"]} == {"h264", "aac"}
    assert got["output"].endswith(".mkv")


def test_guardar_desde_el_menu_crea_los_archivos(cut_mpv, tmp_path):
    """H56 · Ser: «al dar guardar los 3 elegidos en uno, no hace nada, y por separado tampoco». Hasta ahora se
    probaba la LLAMADA (convert.cut) pero no el BOTÓN, que es lo que él pulsa."""
    h, d = cut_mpv
    for a, b in ((3.0, 6.0), (10.0, 13.0)):
        mark_at(h, a)
        mark_at(h, b)
    cut_state(h, lambda v: v["count"] == 2)

    h.command("script-binding", "mu_cut/cut-menu")
    v = cut_state(h, lambda v: any(t.startswith("Guardar los 2 elegidos por separado") for t in titles_cut(v)))
    fila = next(i for i, t in enumerate(titles_cut(v), start=1) if t.startswith("Guardar los 2 elegidos por sep"))
    ev_cut(h, {"type": "activate", "index": fila, "value": {"action": "save"}})

    fin = time.monotonic() + 180
    hechos = []
    while time.monotonic() < fin:
        hechos = [t for t in d.call("convert.list") if t.get("status") == "done"]
        if len(hechos) >= 2:
            break
        time.sleep(0.5)
    assert len(hechos) >= 2, d.call("convert.list")
    for t in hechos[:2]:
        assert Path(t["output"]).is_file(), t
    assert h.script_errors() == [], h.script_errors()
