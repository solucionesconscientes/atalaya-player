"""Capítulos vía IA externa (docs/capitulos-ia/FICHA.md).

F1 · las funciones puras de `mpv-config/script-modules/mu/chapters.lua`: leer el SRT, compactarlo para la IA, leer
con tolerancia lo que la IA contesta, ajustarlo a la transcripción y escribirlo como «MM:SS Título». Se prueban
dentro del Lua de mpv de verdad (un mpv sin ventana con `tests/fixtures/lua/chapters_probe.lua`), no con un
intérprete suelto: en esta máquina no hay ni `luajit` ni `lua5.1`, y lo que importa es lo que mpv acepta.

El SRT de `tests/fixtures/chapters/charla.srt` está escrito a mano (no había ningún SRT en el repo): una charla de
8 minutos con lo que trae un SRT de Whisper — BOM, CRLF, etiquetas, un tiempo con punto, una frase repetida y las
alucinaciones de siempre al final.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.conftest import ROOT, TMP, start_mpv

FIXTURES = ROOT / "tests" / "fixtures"
PROBE = FIXTURES / "lua" / "chapters_probe.lua"
SRT = FIXTURES / "chapters" / "charla.srt"


class Probe:
    """`probe('fit', lista, {...})` → lo que devuelve `mu.chapters.fit(lista, {...})`."""

    def __init__(self, h):
        self.h = h
        self.n = 0

    def __call__(self, fn: str, *args):
        self.n += 1
        rid = f"c{self.n}"
        self.h.command("script-message-to", "chapters_probe", "call", rid, fn,
                       json.dumps(list(args), ensure_ascii=False))
        res = self.h.wait_property(f"user-data/chapters-probe/{rid}", lambda v: v is not None, timeout=10)
        assert res["ok"], res.get("error")
        return res.get("value")


@pytest.fixture(scope="module")
def probe():
    h = start_mpv(TMP / "test-chapters", ["--load-scripts=no", f"--script={PROBE}"])
    try:
        h.wait_property("user-data/chapters-probe/ready", bool, timeout=10)
        yield Probe(h)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def srt_text() -> str:
    return SRT.read_text(encoding="utf-8")   # con el BOM y los CRLF tal cual


def times(chapters) -> list[float]:
    return [round(c["time"], 1) for c in chapters or []]


def titles(chapters) -> list[str]:
    return [c["title"] for c in chapters or []]


# -- SRT ----------------------------------------------------------------------------------------------------------


def test_srt_real_con_bom_crlf_etiquetas_y_punto(probe):
    blocks = probe("parse_srt", srt_text())
    assert len(blocks) == 35
    assert blocks[0]["start"] == pytest.approx(0.0) and blocks[0]["stop"] == pytest.approx(3.2)
    assert blocks[0]["text"] == "Hola a todos y bienvenidos al taller.", "sin BOM ni etiquetas"
    assert blocks[1]["text"] == "Hoy vamos a hacer pan en casa, paso a paso.", "las dos líneas, unidas con un espacio"
    assert blocks[5]["text"] == "Los ingredientes son muy pocos.", "sin la etiqueta {\\an8}"
    assert blocks[6]["start"] == pytest.approx(65.3), "milisegundos con punto"
    assert all("\r" not in b["text"] for b in blocks)


def test_srt_vacio_o_basura_no_revienta(probe):
    assert not probe("parse_srt", "")
    assert not probe("parse_srt", "esto no es un srt\nni de lejos")


def test_srt_sin_numeros_de_bloque_y_con_horas(probe):
    text = "01:02:03,500 --> 01:02:05,000\nUno\n\n01:02:06,000 --> 01:02:08,000\n<b>Dos</b>\n"
    blocks = probe("parse_srt", text)
    assert [round(b["start"], 1) for b in blocks] == [3723.5, 3726.0]
    assert [b["text"] for b in blocks] == ["Uno", "Dos"]


# -- transcripción compacta ---------------------------------------------------------------------------------------


def test_compactado_legible_del_srt_real(probe):
    blocks = probe("parse_srt", srt_text())
    res = probe("compact", blocks, {"duration": 510})
    lines = res["lines"]
    text = "\n".join(lines)
    assert res["text"] == text
    assert all(re.match(r"^\[\d\d:\d\d\] \S", ln) for ln in lines), lines
    assert lines[0].startswith("[00:00] Hola a todos y bienvenidos al taller. Hoy vamos a hacer pan")
    # bloques unidos en líneas de unos 10-15 s: 33 bloques útiles caben en bastantes menos líneas
    assert 10 <= len(lines) <= 25, lines
    # el tiempo de cada línea es el del primer bloque que lleva, y van en orden
    secs = [int(ln[1:3]) * 60 + int(ln[4:6]) for ln in lines]
    assert secs == sorted(secs) and len(set(secs)) == len(secs)
    assert "[01:01] Los ingredientes son muy pocos." in text
    assert "[02:05] Ahora vamos con el amasado." in text
    # la frase repetida por Whisper sale una vez, y nada de etiquetas ni alucinaciones
    assert text.count("Sin máquinas y con un horno normal.") == 1
    for basura in ("<", "{", "Suscríbete", "Gracias por ver", "Amara", "Subtítulos"):
        assert basura not in text, basura


def test_compactado_con_horas_en_todas_las_lineas(probe):
    blocks = probe("parse_srt", srt_text())
    lines = probe("compact", blocks, {"duration": 3700})["lines"]
    assert all(re.match(r"^\[\d:\d\d:\d\d\] ", ln) for ln in lines), lines[:3]
    assert lines[0].startswith("[0:00:00] ")


def test_compactado_ventana_configurable(probe):
    blocks = probe("parse_srt", srt_text())
    cortas = probe("compact", blocks, {"duration": 510, "line_seconds": 4})["lines"]
    largas = probe("compact", blocks, {"duration": 510, "line_seconds": 30})["lines"]
    assert len(cortas) > len(largas)


def test_prompt_y_tokens(probe):
    blocks = probe("parse_srt", srt_text())
    lines = probe("compact", blocks, {"duration": 512})["lines"]
    text = probe("prompt", "Eres un editor.\n", 512, lines)
    assert text.startswith("Eres un editor.\n\nDURACIÓN TOTAL: 0:08:32\n\nTRANSCRIPCIÓN:\n[00:00] Hola")
    assert text.endswith(lines[-1])
    n = probe("tokens", text)
    assert isinstance(n, (int, float)) and len(text) / 6 < n < len(text)


def test_reloj(probe):
    assert probe("clock", 0) == "00:00"
    assert probe("clock", 462.9) == "07:42"
    assert probe("clock", 3723, True) == "1:02:03"
    assert probe("clock", 75, True) == "0:01:15"


# -- respuesta de la IA -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("linea", [
    "00:00 Cómo empezar",
    "- 00:00 Cómo empezar",
    "* 00:00 - Cómo empezar",
    "• 00:00 – Cómo empezar",
    "1. 00:00 — Cómo empezar",
    "1) 00:00: Cómo empezar",
    "**00:00** Cómo empezar",
    "**00:00 Cómo empezar**",
    "[00:00] Cómo empezar",
    "(00:00) Cómo empezar",
    "00:00 | Cómo empezar",
    '00:00 "Cómo empezar."',
    "00:00 «Cómo empezar»",
    "00:00 `Cómo empezar`",
    "  0:00   Cómo empezar  ",
    "00:00 - 07:42 Cómo empezar",
])
def test_respuesta_en_todos_los_formatos(probe, linea):
    res = probe("parse_reply", f"{linea}\n07:42 Segundo tema")
    assert times(res) == [0, 462] and titles(res) == ["Cómo empezar", "Segundo tema"], res


def test_respuesta_con_bloque_de_codigo_y_texto_alrededor(probe):
    text = ("¡Claro! Aquí tienes los capítulos:\r\n\r\n```text\r\n00:00 Presentación del taller\r\n"
            "01:01 Los ingredientes\r\n02:05 Amasado a mano\r\n```\r\n\r\n¿Quieres algo más?")
    res = probe("parse_reply", text)
    assert times(res) == [0, 61, 125]
    assert titles(res) == ["Presentación del taller", "Los ingredientes", "Amasado a mano"]


def test_respuesta_con_horas(probe):
    res = probe("parse_reply", "0:00:00 Uno\n0:59:30 Dos\n1:02:03 Tres\n10:15:00 Cuatro")
    assert times(res) == [0, 3570, 3723, 36900]


def test_titulo_conserva_interrogacion_y_quita_puntos_finales(probe):
    res = probe("parse_reply", "00:00 ¿Por qué sube el pan?\n03:00 La sal, siempre aparte...\n05:00 Fin;")
    assert titles(res) == ["¿Por qué sube el pan?", "La sal, siempre aparte", "Fin"]


@pytest.mark.parametrize("basura", [
    "",
    "   \n\n",
    "Lo siento, no puedo ayudarte con eso.",
    "Capítulo uno: introducción\nCapítulo dos: desarrollo",
    "99:99 Tiempo imposible\n12:75 Tampoco",
    "00:00\n05:00",                       # tiempos sin título
])
def test_respuesta_basura_da_lista_vacia(probe, basura):
    assert not probe("parse_reply", basura)


# -- validación y ajuste ------------------------------------------------------------------------------------------


def ch(*pares):
    return [{"time": t, "title": title} for t, title in pares]


def test_descarta_fuera_de_rango_duplicados_y_ordena(probe):
    lista = ch((300, "C"), (0, "A"), (120, "B"), (120, "B repetido"), (900, "Fuera"))
    res = probe("fit", lista, {"duration": 600})
    assert times(res["chapters"]) == [0, 120, 300]
    assert titles(res["chapters"]) == ["A", "B", "C"]
    assert res["suspicious"] is False


def test_separacion_minima_conserva_el_anterior(probe):
    res = probe("fit", ch((0, "A"), (30, "B"), (120, "C"), (150, "D"), (200, "E")), {"duration": 600})
    assert titles(res["chapters"]) == ["A", "C", "E"]
    res = probe("fit", ch((0, "A"), (30, "B")), {"duration": 600, "min_gap": 10})
    assert titles(res["chapters"]) == ["A", "B"]


def test_sin_cero_inserta_inicio(probe):
    res = probe("fit", ch((300, "Tema"), (120, "Otro")), {"duration": 600})
    assert times(res["chapters"]) == [0, 120, 300]
    assert titles(res["chapters"]) == ["Inicio", "Otro", "Tema"]
    res = probe("fit", ch((300, "Tema")), {"duration": 600, "first_title": "Start"})
    assert titles(res["chapters"]) == ["Start", "Tema"]


def test_primer_capitulo_casi_en_cero_se_lleva_al_cero(probe):
    """Un primer capítulo a pocos segundos del principio ES el principio: se lleva al 0 en vez de meter un «Inicio»
    de 10 segundos que la separación mínima acabaría quitando junto con el título bueno."""
    res = probe("fit", ch((10, "Presentación"), (200, "Tema")), {"duration": 600})
    assert times(res["chapters"]) == [0, 200]
    assert titles(res["chapters"]) == ["Presentación", "Tema"]


def test_lista_vacia_no_inventa_inicio(probe):
    assert not probe("fit", [], {"duration": 600})["chapters"]
    assert not probe("fit", ch((900, "Fuera")), {"duration": 600})["chapters"]


STARTS = [0.0, 3.4, 61.2, 65.3, 125.7, 130.5, 210.0, 300.4, 380.2, 470.0]


def test_ajuste_al_inicio_de_bloque_mas_cercano(probe):
    lista = ch((0, "Inicio del taller"), (65, "Ingredientes"), (140, "Amasado"), (240, "Fermentación"),
               (382, "Horno"))
    res = probe("fit", lista, {"duration": 510, "starts": STARTS})
    # 65 → 65.3 (el más cercano, no 61.2); 140 → 130.5 está a 9,5 s; 240 → 210 está a 30 s: se queda; 382 → 380.2
    assert times(res["chapters"]) == [0, 65.3, 130.5, 240, 380.2]
    assert res["suspicious"] is False
    assert res["total"] == 4 and res["matched"] == 3, "el 0 no cuenta: lo pide el prompt, no sale del texto"


def test_tolerancia_configurable(probe):
    res = probe("fit", ch((0, "A"), (140, "B")), {"duration": 510, "starts": STARTS, "tolerance": 5})
    assert times(res["chapters"]) == [0, 140]


def test_el_cero_no_se_mueve_aunque_haya_un_bloque_cerca(probe):
    res = probe("fit", ch((0, "A"), (125, "B")), {"duration": 510, "starts": [4.0, 125.7]})
    assert times(res["chapters"]) == [0, 125.7]


def test_tiempos_que_no_son_de_esta_transcripcion(probe):
    lejos = ch((0, "A"), (100, "B"), (170, "C"), (260, "D"), (420, "E"))      # ninguno a <= 15 s de un bloque
    res = probe("fit", lejos, {"duration": 510, "starts": STARTS})
    assert res["suspicious"] is True and res["matched"] == 0 and res["total"] == 4
    assert times(res["chapters"]) == [0, 100, 170, 260, 420], "sin ajuste: se enseñan tal cual para confirmar"
    # exactamente la mitad no es «más de la mitad»
    mitad = ch((0, "A"), (62, "B"), (100, "C"), (126, "D"), (260, "E"))
    assert probe("fit", mitad, {"duration": 510, "starts": STARTS})["suspicious"] is False


def test_sin_srt_no_hay_ajuste_ni_control(probe):
    lejos = ch((0, "A"), (100, "B"), (170, "C"))
    res = probe("fit", lejos, {"duration": 510})
    assert res["suspicious"] is False and times(res["chapters"]) == [0, 100, 170]


# -- formato del archivo ------------------------------------------------------------------------------------------


def test_formato_y_vuelta(probe):
    lista = ch((0, "Inicio"), (61.2, "Ingredientes"), (462, "Fin"))
    text = probe("format", lista, 510)
    assert text == "00:00 Inicio\n01:01 Ingredientes\n07:42 Fin\n"
    assert times(probe("parse_reply", text)) == [0, 61, 462]
    largo = probe("format", ch((0, "A"), (3723, "B")), 4000)
    assert largo == "0:00:00 A\n1:02:03 B\n"
    assert times(probe("parse_reply", largo)) == [0, 3723]


def test_flujo_completo_con_el_srt_real(probe):
    """Lo que hará el atajo B: la respuesta de una IA para esta charla, ajustada a sus bloques."""
    blocks = probe("parse_srt", srt_text())
    starts = [b["start"] for b in blocks]
    reply = ("```\n00:00 Presentación del taller\n01:01 Los ingredientes justos\n02:05 Amasado a mano\n"
             "03:30 La fermentación\n05:00 Formar el pan\n06:20 Al horno\n07:50 Despedida\n```")
    res = probe("fit", probe("parse_reply", reply), {"duration": 510, "starts": starts})
    assert res["suspicious"] is False and res["matched"] == res["total"] == 6
    assert times(res["chapters"]) == [0, 61.2, 125.7, 210.0, 300.4, 380.2, 470.0]
    assert probe("format", res["chapters"], 510).splitlines()[1] == "01:01 Los ingredientes justos"
