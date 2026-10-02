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
