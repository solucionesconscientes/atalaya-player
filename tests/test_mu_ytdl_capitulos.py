"""F2 · las dos pantallas nuevas de mu-ytdl con mpv de verdad: «Capítulos» y «Fragmentos».

Se abre el menú de descarga como lo abriría una persona (la tecla de «Descargar» del propio script), se entra en las
dos vistas nuevas y se comprueba lo que se ve: las filas nuevas en «Descargar», el estado vacío honesto de un vídeo sin
capítulos y, en fragmentos, que añadir un trozo desde el punto actual pide la ficha previa a mpvd (`ytdl.sections.preview`)
y pinta el aviso de precisión del corte.

El yt-dlp es el falso de los tests (sin red) con los medios locales: el guion y el vídeo son reales, la descarga no.
"""

from __future__ import annotations

import re

from tests.test_mu_ytdl import URL, send_event, wait_view, ytdl_mpv  # noqa: F401 - fixture


def _titulos(st):
    return [i["title"] for i in st.get("items", [])]


def test_las_dos_pantallas_nuevas_se_abren_desde_descargar_y_dicen_lo_que_hay(ytdl_mpv):  # noqa: F811
    h, d, _arglog, _tmp = ytdl_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    h.command("loadfile", URL)
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True, timeout=40)
    # 0. el RPC existe y contesta (si esto falla, la vista no puede pintar nada)
    res = d.call("ytdl.chapters", {"url": URL})
    assert res["count"] == 0 and res["capitulos"] == []

    # 1. las dos vistas cuelgan de «Descargar»: sin eso solo llegarían por una tecla que nadie se sabe
    h.command("script-binding", "mu_ytdl/ytdl-download")
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "download"
                         and "Capítulos…" in [i["title"] for i in v.get("items", [])], timeout=40)
    titulos = _titulos(st)
    assert "Capítulos…" in titulos and "Fragmentos de tiempo…" in titulos

    # 2. capítulos: el vídeo de prueba no trae ninguno, así que la pantalla lo dice y ofrece lo que sí se puede hacer
    send_event(h, {"type": "activate", "index": 0, "value": {"view": "chapters"}})
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "chapters"
                         and "Este vídeo no tiene capítulos" in [i["title"] for i in v.get("items", [])], timeout=40)
    assert "Descargar el vídeo entero" in _titulos(st)
    assert h.script_errors() == []

    # 3. fragmentos: se añade desde el punto actual (sin escribir tiempos) y la ficha previa llega de mpvd
    h.command("set_property", "time-pos", 10)
    send_event(h, {"type": "activate", "index": 0, "value": {"view": "fragments"}})
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "fragments"
                    and "Añadir un fragmento desde aquí…" in [i["title"] for i in v.get("items", [])], timeout=40)
    send_event(h, {"type": "activate", "index": 1, "value": {"frag_add": 30}})
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "fragments"
                         and any(re.match(r"^\d+:\d\d – \d+:\d\d$", i["title"]) for i in v.get("items", [])),
                         timeout=60)
    # el trozo empieza donde estaba el vídeo y acaba 30 s después (o al final, que son 30 s)
    assert re.match(r"^0:10 – 0:30$", next(t for t in _titulos(st) if " – " in t))
    assert "Descargar el fragmento" in _titulos(st)
    # el aviso de precisión es el que ha devuelto mpvd para estos códecs (H.264 de vídeo + FLAC de audio)
    avisos = [i["title"] for i in st["items"] if i.get("hint") == "" and "corte" in i["title"]]
    assert any("fotograma clave" in a for a in avisos), avisos
    assert h.script_errors() == []
