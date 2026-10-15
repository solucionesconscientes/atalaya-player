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
import shutil
import subprocess
from pathlib import Path

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


# == F2 · integración en mpv: mu-chapters ==========================================================================
#
# Un vídeo de 8 min 30 s (negro, 1 fps: se genera en un segundo) con la charla al lado como `charla.srt`, que es lo
# que deja «Guardar subtítulos (SRT)». El portapapeles del sistema no existe en un mpv sin ventana, así que los tests
# usan el de prueba: `mu-chapters-test_clipboard=yes` → `user-data/mu/chapters-clipboard` hace de portapapeles.

OPTS = "--script-opts=mu-core-autostart=no,mu-chapters-test_clipboard=yes"
REPLY = ("```\n00:00 Presentación del taller\n01:01 Los ingredientes justos\n02:05 Amasado a mano\n"
         "03:30 La fermentación\n05:00 Formar el pan\n06:20 Al horno\n07:50 Despedida\n```")
REPLY_TITLES = ["Presentación del taller", "Los ingredientes justos", "Amasado a mano", "La fermentación",
                "Formar el pan", "Al horno", "Despedida"]
# a más de 15 s de cualquier bloque de la charla: no pueden salir de esta transcripción
LEJOS = "00:00 Uno\n01:47 Dos\n03:00 Tres\n04:26 Cuatro\n05:50 Cinco\n07:17 Seis"


@pytest.fixture(scope="module")
def charla_mkv(tmp_path_factory):
    out = tmp_path_factory.mktemp("charla") / "charla.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:r=1:d=510",
                    "-c:v", "mpeg4", str(out)], check=True, timeout=120)
    return out


@pytest.fixture
def video(charla_mkv, tmp_path):
    """La charla sin transcripción al lado (los tests que la quieren copian el SRT con `con_srt`)."""
    v = tmp_path / "charla.mkv"
    shutil.copyfile(charla_mkv, v)
    return v


def con_srt(v):
    shutil.copyfile(SRT, v.with_suffix(".srt"))
    return v


def capitulos_txt(v):
    return v.with_name(v.stem + ".capitulos.txt")


def abrir(v, *opts: str, min_duration: float = 500):
    h = start_mpv(TMP / "test-chapters", [OPTS + "".join("," + o for o in opts), "--keep-open=yes", "--pause=yes",
                                          str(v)])
    h.wait_property("duration", lambda d: isinstance(d, (int, float)) and d > min_duration, timeout=20)
    h.wait_property("user-data/mu/chapters", lambda st: bool(st) and st.get("path") == str(v), timeout=20)
    return h


def estado(h):
    return h.get("user-data/mu/chapters") or {}


def osd_de(h, texto: str, timeout: float = 10):
    return h.wait_property("user-data/mu/chapters", lambda st: bool(st) and texto in (st.get("osd") or ""),
                           timeout=timeout)


def leer_portapapeles(h):
    try:
        return h.get("user-data/mu/chapters-clipboard")
    except Exception:  # noqa: BLE001 - unset user-data is "property unavailable"
        return None


def pegar_en_portapapeles(h, text: str) -> None:
    h.command("set_property", "user-data/mu/chapters-clipboard", text)


def titulos(h):
    return [c["title"] for c in h.get("chapter-list") or []]


def test_copiar_para_la_ia_con_la_tecla(video):
    v = con_srt(video)
    h = abrir(v)
    try:
        h.command("keypress", "ctrl+alt+c")
        st = osd_de(h, "Copiado: ")
        text = leer_portapapeles(h)
        assert text.startswith("Eres un editor de vídeo experto"), text[:80]
        assert re.search(r"\n\nDURACIÓN TOTAL: 0:08:(29|30)\n\nTRANSCRIPCIÓN:\n\[00:00\] Hola a todos", text)
        assert "Amara" not in text and "<i>" not in text
        n = text.count("\n[")
        assert f"Copiado: {n} líneas (~1 mil tokens)" in st["osd"], st["osd"]
        assert st["status"] == "copied"
        assert not capitulos_txt(v).exists(), "copiar no escribe nada junto al vídeo"
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_pegar_aplica_guarda_y_al_reabrir_se_cargan_solos(video):
    v = con_srt(video)
    txt = capitulos_txt(v)
    bak = txt.with_name(txt.name + ".bak")
    h = abrir(v)
    try:
        pegar_en_portapapeles(h, REPLY)
        h.command("keypress", "ctrl+alt+p")
        st = osd_de(h, "7 capítulos aplicados")
        assert st["status"] == "applied" and st["source"] == "paste" and st["count"] == 7
        assert st["file"] == str(txt)
        cl = h.wait_property("chapter-list", lambda c: len(c or []) == 7, timeout=10)
        assert [round(c["time"], 1) for c in cl] == [0, 61.2, 125.7, 210.0, 300.4, 380.2, 470.0], "ajustados al SRT"
        assert [c["title"] for c in cl] == REPLY_TITLES
        assert txt.read_text(encoding="utf-8").splitlines() == [
            "00:00 Presentación del taller", "01:01 Los ingredientes justos", "02:05 Amasado a mano",
            "03:30 La fermentación", "05:00 Formar el pan", "06:20 Al horno", "07:50 Despedida"]
        assert not bak.exists(), "la primera vez no hay nada que guardar de copia"

        # reabrir el vídeo: los capítulos salen del archivo al instante, sin pegar nada
        pegar_en_portapapeles(h, "")
        h.command("loadfile", str(v), "replace")
        st = h.wait_property("user-data/mu/chapters", lambda s: bool(s) and s.get("source") == "file"
                             and s.get("count") == 7, timeout=10)
        cl = h.wait_property("chapter-list", lambda c: [x["time"] for x in c or []] == [0, 61, 125, 210, 300, 380, 470],
                             timeout=10)
        assert [c["title"] for c in cl] == REPLY_TITLES

        # pegar otra vez guarda una copia .bak de lo que había
        pegar_en_portapapeles(h, "00:00 Uno\n05:00 Dos")
        h.command("keypress", "ctrl+alt+p")
        h.wait_property("chapter-list", lambda c: [x["title"] for x in c or []] == ["Uno", "Dos"], timeout=10)
        assert txt.read_text(encoding="utf-8") == "00:00 Uno\n05:00 Dos\n"
        assert bak.read_text(encoding="utf-8").startswith("00:00 Presentación del taller\n")
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_sin_srt_lo_dice_y_no_copia_nada(video):
    h = abrir(video)
    try:
        h.command("keypress", "ctrl+alt+c")
        st = osd_de(h, "alt+S")
        assert "transcripción" in st["osd"]
        assert not leer_portapapeles(h)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_sin_srt_se_puede_pegar_sin_ajuste(video):
    h = abrir(video)
    try:
        pegar_en_portapapeles(h, REPLY)
        h.command("keypress", "ctrl+alt+p")
        osd_de(h, "7 capítulos aplicados")
        cl = h.wait_property("chapter-list", lambda c: len(c or []) == 7, timeout=10)
        assert [c["time"] for c in cl] == [0, 61, 125, 210, 300, 380, 470], "sin SRT no hay a qué ajustar"
        assert capitulos_txt(video).exists()
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


@pytest.mark.parametrize("pegado, aviso", [("", "vacío"), ("   \n", "vacío"),
                                           ("Lo siento, no puedo ayudarte con eso.", "No reconozco")])
def test_portapapeles_vacio_o_basura_lo_dice_y_mpv_sigue(video, pegado, aviso):
    v = con_srt(video)
    h = abrir(v)
    try:
        pegar_en_portapapeles(h, pegado)
        h.command("keypress", "ctrl+alt+p")
        osd_de(h, aviso)
        assert not h.get("chapter-list")
        assert not capitulos_txt(v).exists()
        h.command("set_property", "pause", False)
        h.wait_property("time-pos", lambda t: isinstance(t, (int, float)) and t > 0.5, timeout=10)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def confirmar(h, respuesta: str) -> None:
    """Contesta al diálogo de confirmación de la casa (`mu-confirm` de mu-menu), como haría uosc al elegir."""
    req = h.wait_property("user-data/mu/confirm_request", lambda r: bool(r) and r.get("token"), timeout=10)
    ev = {"type": "activate", "value": {"token": req["token"], "answer": respuesta}}
    h.command("script-message-to", "mu_menu", "mu-menu-confirm-event", json.dumps(ev))


def test_tiempos_sospechosos_piden_confirmacion(video):
    v = con_srt(video)
    txt = capitulos_txt(v)
    h = abrir(v)
    try:
        h.wait_property("user-data/mu/core", lambda c: bool(c) and c.get("uosc"), timeout=40)
        pegar_en_portapapeles(h, LEJOS)
        h.command("keypress", "ctrl+alt+p")
        req = h.wait_property("user-data/mu/confirm_request",
                              lambda r: bool(r) and "no parecen de esta transcripción" in (r.get("text") or ""),
                              timeout=10)
        assert "5 de 5" in req["text"], req["text"]
        h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-confirm", timeout=10)
        assert estado(h)["status"] == "confirm"
        assert not h.get("chapter-list") and not txt.exists(), "nada se aplica antes de contestar"

        confirmar(h, "no")
        osd_de(h, "No se han aplicado")
        assert not h.get("chapter-list") and not txt.exists()

        h.command("keypress", "ctrl+alt+p")
        h.wait_property("user-data/mu/confirm_request",
                        lambda r: bool(r) and r.get("token") != req["token"], timeout=10)
        confirmar(h, "yes")
        cl = h.wait_property("chapter-list", lambda c: len(c or []) == 6, timeout=10)
        assert [c["time"] for c in cl] == [0, 107, 180, 266, 350, 437]
        assert txt.exists()
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


@pytest.mark.parametrize("respetar", [True, False])
def test_capitulos_propios_no_se_pisan_si_respect_existing(media_dir, tmp_path, respetar):
    v = tmp_path / "con capítulos.mkv"
    shutil.copyfile(media_dir / "chapters.mkv", v)
    opts = ["mu-chapters-min_gap=5"] + ([] if respetar else ["mu-chapters-respect_existing=no"])
    h = abrir(v, *opts, min_duration=20)
    try:
        h.wait_property("chapter-list", lambda c: len(c or []) == 3, timeout=10)
        propios = titulos(h)
        pegar_en_portapapeles(h, "00:00 Mi uno\n00:20 Mi dos")
        h.command("keypress", "ctrl+alt+p")
        if respetar:
            osd_de(h, "ya trae sus propios capítulos")
            assert titulos(h) == propios
            assert not capitulos_txt(v).exists()
        else:
            h.wait_property("chapter-list", lambda c: [x["title"] for x in c or []] == ["Mi uno", "Mi dos"],
                            timeout=10)
            assert capitulos_txt(v).exists()
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_en_directo_no_hace_nada(tmp_path):
    stream = "av://lavfi:sine=frequency=440"
    h = start_mpv(TMP / "test-chapters", [OPTS, stream])
    try:
        h.wait_property("user-data/mu/chapters", lambda st: bool(st) and st.get("path") == stream, timeout=20)
        h.command("keypress", "ctrl+alt+c")
        osd_de(h, "solo para archivos del equipo")
        assert not leer_portapapeles(h)
        pegar_en_portapapeles(h, REPLY)
        h.command("keypress", "ctrl+alt+p")
        h.wait_property("user-data/mu/chapters", lambda st: st.get("status") != "applied", timeout=5)
        assert not h.get("chapter-list")
        assert estado(h)["count"] == 0
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def item(h, view: str, texto: str):
    v = h.wait_property("user-data/mu/menu", lambda m: bool(m) and m.get("view") == view
                        and any(texto in i["title"] for i in m.get("items", [])), timeout=15)
    return next(i for i in v["items"] if texto in i["title"])


def activar(h, it) -> None:
    ev = {"type": "activate", "value": it["value"], "menu_id": "{root}", "is_pointer": False, "alt": False,
          "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_menu", "mu-menu-event", json.dumps(ev))


def test_desde_el_menu_sin_recordar_teclas(video):
    """En este reproductor todo se puede hacer sin saberse las teclas: Menú → Subtítulos → Capítulos con una IA."""
    v = con_srt(video)
    h = abrir(v)
    try:
        h.wait_property("user-data/mu/core", lambda c: bool(c) and c.get("uosc"), timeout=40)
        h.command("script-binding", "mu_menu/root")
        activar(h, item(h, "root", "Subtítulos"))
        activar(h, item(h, "subs", "Capítulos con una IA"))
        copiar = item(h, "chapters", "Copiar la transcripción")
        assert copiar["hint"] == "ctrl+alt+c"
        assert item(h, "chapters", "Pegar los capítulos")["hint"] == "ctrl+alt+p"
        activar(h, copiar)
        osd_de(h, "Copiado: ")
        assert leer_portapapeles(h).startswith("Eres un editor de vídeo")
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


# == F3 · pulido: el prompt editable y quitar los capítulos =========================================================
#
# `prompt-ia.txt` vive junto al módulo (`mpv-config/script-modules/mu/`) y se lee en cada copia: quien lo edite ve el
# cambio sin reiniciar. El de dentro del script es la red de seguridad si el archivo no está.


def test_el_prompt_se_lee_del_archivo_y_se_relee_en_cada_copia(video, tmp_path):
    v = con_srt(video)
    mio = tmp_path / "mi-prompt.txt"
    mio.write_text("Prompt mío, con acentos y ñ.\n", encoding="utf-8")
    h = abrir(v, f"mu-chapters-prompt_file={mio}")
    try:
        h.command("keypress", "ctrl+alt+c")
        osd_de(h, "Copiado: ")
        assert leer_portapapeles(h).startswith("Prompt mío, con acentos y ñ.\n\nDURACIÓN TOTAL: ")
        mio.write_text("Otro prompt, ya cambiado.\n", encoding="utf-8")
        h.command("keypress", "ctrl+alt+c")
        h.wait_property("user-data/mu/chapters-clipboard",
                        lambda t: bool(t) and t.startswith("Otro prompt, ya cambiado.\n\nDURACIÓN TOTAL: "), timeout=10)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_sin_archivo_de_prompt_se_copia_el_de_dentro(video, tmp_path):
    v = con_srt(video)
    h = abrir(v, f"mu-chapters-prompt_file={tmp_path / 'no-existe.txt'}")
    try:
        h.command("keypress", "ctrl+alt+c")
        osd_de(h, "Copiado: ")
        assert leer_portapapeles(h).startswith("Eres un editor de vídeo experto en estructurar contenido"), \
            leer_portapapeles(h)[:60]
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_quitar_capitulos_pregunta_y_borra_el_archivo(video):
    v = con_srt(video)
    txt = capitulos_txt(v)
    h = abrir(v)
    try:
        h.wait_property("user-data/mu/core", lambda c: bool(c) and c.get("uosc"), timeout=40)
        pegar_en_portapapeles(h, REPLY)
        h.command("keypress", "ctrl+alt+p")
        h.wait_property("chapter-list", lambda c: len(c or []) == 7, timeout=10)
        assert txt.exists()

        h.command("keypress", "ctrl+alt+d")
        req = h.wait_property("user-data/mu/confirm_request",
                              lambda r: bool(r) and "Quitar los capítulos" in (r.get("text") or ""), timeout=10)
        assert estado(h)["status"] == "confirm"
        confirmar(h, "no")
        osd_de(h, "Se quedan como estaban")
        assert len(h.get("chapter-list") or []) == 7 and txt.exists(), "decir que no no quita nada"

        h.command("keypress", "ctrl+alt+d")
        h.wait_property("user-data/mu/confirm_request",
                        lambda r: bool(r) and r.get("token") != req["token"], timeout=10)
        confirmar(h, "yes")
        h.wait_property("chapter-list", lambda c: not c, timeout=10)
        assert not txt.exists(), "el archivo de al lado se va con ellos"
        assert estado(h)["status"] == "cleared" and estado(h)["count"] == 0
        osd_de(h, "borrado el archivo de al lado")

        h.command("keypress", "ctrl+alt+d")          # ya no hay nada: lo dice, sin preguntar
        osd_de(h, "no tiene capítulos que quitar")
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


# == F4 · conseguir la transcripción y copiarla =====================================================================
#
# El trabajo lo hace mu-subs (los subtítulos de texto del vídeo, o whisper) y aquí solo se pide y se cuenta. Los dos
# caminos se prueban: el de los subtítulos del vídeo entero y de verdad, y el de whisper hasta donde se puede en una
# máquina donde el whisper.cpp vendorizado no arranca (GLIBC): que se pida, que se decida whisper y que el progreso —o
# el motivo del fallo— se vean. Lo que no puede pasar nunca es que se quede callado.

CLIP = "mu-chapters-test_clipboard=yes"
OPTS_MPVD = f"--script-opts={CLIP}"


@pytest.fixture
def con_mpvd(daemon_env):
    """Un mpv con mpvd de verdad (mu-subs guarda con `subs.save`, que es cosa del daemon)."""

    def _abrir(v, *opts: str, min_duration: float = 500):
        h = start_mpv(daemon_env.runtime_dir, [
            OPTS_MPVD + "".join("," + o for o in opts), "--keep-open=yes", "--pause=yes", str(v)],
            env=daemon_env.env)
        h.wait_property("duration", lambda d: isinstance(d, (int, float)) and d > min_duration, timeout=20)
        h.wait_property("user-data/mu/core", lambda c: bool(c) and c.get("mpvd") == "connected", timeout=40)
        h.wait_property("user-data/mu/chapters", lambda st: bool(st) and st.get("path") == str(v), timeout=20)
        return h

    return _abrir


def pedir_transcripcion(h) -> None:
    """El botón, tal cual está en el menú (sin tecla propia): Menú → Subtítulos → Capítulos con una IA."""
    h.command("script-binding", "mu_chapters/chapters-transcript")


def test_un_boton_usa_los_subtitulos_del_video_y_avisa(con_mpvd, video):
    v = con_srt(video)
    h = con_mpvd(v)
    try:
        pedir_transcripcion(h)
        st = h.wait_property("user-data/mu/chapters",
                             lambda s: bool(s) and (s.get("transcript") or {}).get("status") == "done", timeout=90)
        t = st["transcript"]
        assert t["source"] == "track", "el vídeo ya trae subtítulos de texto: no se transcribe nada"
        assert t["lines"] > 0 and t["chars"] > 0, t
        assert "Transcripción lista" in st["osd"], st["osd"]
        assert Path(t["out"]).is_file()
        # y de ahí sale la copia de F2, sin hacer nada más
        h.command("keypress", "ctrl+alt+c")
        copiado = osd_de(h, "Copiado: ")
        assert leer_portapapeles(h).startswith("Eres un editor de vídeo")
        assert f"Copiado: {copiado['transcript']['lines']} líneas" in copiado["osd"], copiado["osd"]
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_sin_subtitulos_el_boton_pide_whisper_y_no_se_calla(con_mpvd, video):
    h = con_mpvd(video)
    try:
        pedir_transcripcion(h)
        # mu-subs decide por su cuenta: no hay pista de texto, así que whisper y el modelo del tier de hardware
        st = h.wait_property("user-data/mu/chapters",
                             lambda s: bool(s) and (s.get("transcript") or {}).get("source") == "ai", timeout=30)
        subs = h.wait_property("user-data/mu/subs",
                               lambda s: bool(s) and s.get("transcript_status") in
                               ("transcribing", "saving", "done", "failed"), timeout=60)
        if subs["transcript_status"] == "failed":
            # en esta máquina el whisper-cli vendorizado no arranca (GLIBC): lo que se exige es que lo diga
            st = osd_de(h, "No se pudo conseguir la transcripción", timeout=60)
            assert st["transcript"]["error"], st["transcript"]
        else:
            assert subs["transcript_source"] == "ai"
            h.wait_property("user-data/mu/chapters",
                            lambda s: bool(s) and (s.get("transcript") or {}).get("status") == "transcribing", timeout=30)
        # y el reproductor sigue entero
        h.command("set_property", "pause", False)
        h.wait_property("time-pos", lambda t: isinstance(t, (int, float)) and t > 0.5, timeout=10)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_en_directo_no_pide_ninguna_transcripcion(tmp_path):
    stream = "av://lavfi:sine=frequency=440"
    h = start_mpv(TMP / "test-chapters", [OPTS, stream])
    try:
        h.wait_property("user-data/mu/chapters", lambda st: bool(st) and st.get("path") == stream, timeout=20)
        pedir_transcripcion(h)
        st = osd_de(h, "solo para archivos del equipo")
        assert not st.get("transcript"), "en un directo no se pide nada"
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_las_instrucciones_dentro_de_la_app(video):
    """F4 §5 · los cuatro pasos y los botones, dentro del menú: quien no sabe nada de IA no sale del reproductor."""
    v = con_srt(video)
    h = abrir(v)
    try:
        h.wait_property("user-data/mu/core", lambda c: bool(c) and c.get("uosc"), timeout=40)
        h.command("script-binding", "mu_menu/root")
        activar(h, item(h, "root", "Subtítulos"))
        activar(h, item(h, "subs", "Capítulos con una IA"))
        activar(h, item(h, "chapters", "Cómo funciona"))
        ayuda = h.wait_property("user-data/mu/menu", lambda m: bool(m) and m.get("view") == "chapters_help"
                                and any(i["title"].startswith("4 ·") for i in m.get("items", [])), timeout=15)
        titulos = [i["title"] for i in ayuda["items"]]
        assert [t[0] for t in titulos[:4]] == ["1", "2", "3", "4"], titulos
        assert "ChatGPT" in titulos[2] and "Duck.ai" in titulos[2]
        activar(h, item(h, "chapters_help", "Copiar la transcripción y el prompt"))
        osd_de(h, "Copiado: ")
        assert leer_portapapeles(h).startswith("Eres un editor de vídeo")
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


@pytest.fixture(scope="module")
def charla_con_subs_dentro(charla_mkv, tmp_path_factory):
    """La charla con la transcripción DENTRO del mkv, como pista de texto: ese es el camino de `subs.extract`."""
    out = tmp_path_factory.mktemp("charla-subs") / "charla.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(charla_mkv), "-i", str(SRT), "-map", "0:v", "-map", "1:0",
                    "-c", "copy", "-c:s", "srt", "-metadata:s:s:0", "language=spa", str(out)],
                   check=True, timeout=120)
    return out


def test_pista_dentro_del_video_se_extrae_y_vale_aunque_no_quepa_al_lado(daemon_env, charla_con_subs_dentro, tmp_path):
    """Dos cosas de golpe: la pista de texto de dentro se extrae y se usa (diciéndolo), y si el vídeo está en una
    carpeta donde no se puede escribir —mpvd deja el SRT en la carpeta de subtítulos— la copia lo encuentra igual."""
    ro = tmp_path / "solo-lectura"
    ro.mkdir()
    v = ro / "charla.mkv"
    shutil.copyfile(charla_con_subs_dentro, v)
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    ro.chmod(0o555)
    h = start_mpv(daemon_env.runtime_dir, [OPTS_MPVD, "--keep-open=yes", "--pause=yes", str(v)],
                  env={**daemon_env.env, "MPV_UOS_SUBS_SAVE_DIR": str(fuera)})
    try:
        h.wait_property("duration", lambda d: isinstance(d, (int, float)) and d > 500, timeout=20)
        h.wait_property("user-data/mu/core", lambda c: bool(c) and c.get("mpvd") == "connected", timeout=40)
        h.wait_property("user-data/mu/chapters", lambda st: bool(st) and st.get("path") == str(v), timeout=20)
        pedir_transcripcion(h)
        st = h.wait_property("user-data/mu/chapters",
                             lambda s: bool(s) and (s.get("transcript") or {}).get("status") == "done", timeout=90)
        t = st["transcript"]
        assert t["source"] == "track" and t["lines"] > 0, t
        assert Path(t["out"]).parent == fuera, f"se guardó en {t['out']}"
        assert not (ro / "charla.es.srt").exists(), "en una carpeta de solo lectura no se escribe nada"
        h.command("keypress", "ctrl+alt+c")           # sin SRT al lado: se lee el que dejó la transcripción
        osd_de(h, "Copiado: ")
        assert leer_portapapeles(h).startswith("Eres un editor de vídeo")
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
        ro.chmod(0o755)


def test_sin_subtitulos_la_transcripcion_ya_hecha_se_guarda_y_se_avisa(con_mpvd, daemon_env, video):
    """El camino de la transcripción (vídeo sin pistas de texto) hasta el final, sin depender del motor: se le manda a
    mu-subs el aviso que manda mpvd cuando una transcripción termina —`mu-event` con la tarea de verdad, la de
    `asr.inject`— y se comprueba lo que encadena F4: guardarla sola al estar lista, decir cuántas líneas van y dejar el
    prompt copiable. Lo que no se prueba aquí es que whisper decodifique: eso es de mpvd y tiene sus tests. En esta
    máquina el motor no arranca (GLIBC), así que sin esto el camino de la IA solo se podría probar por el lado del
    fallo (el test de arriba)."""
    segs = [{"start": 0.5, "end": 9.0, "text": "Bienvenidos al taller de pan."},
            {"start": 9.0, "end": 20.0, "text": "Hoy hacemos pan en casa, paso a paso."},
            {"start": 20.0, "end": 32.0, "text": "Los ingredientes son muy pocos."}]
    daemon_env.cli("ensure")
    daemon_env.wait(daemon_env.alive, timeout=30)
    tarea = daemon_env.call("asr.inject", {"path": str(video), "segments": segs, "duration": 510.0})
    assert tarea["complete"] and tarea["cues"] == 3, tarea

    h = con_mpvd(video)
    try:
        # el `path` va vacío porque mu-subs solo acepta el de la tarea cuando no ha empezado ninguna, y aquí no se ha
        # empezado: la transcripción ya estaba hecha.
        h.command("script-message-to", "mu_subs", "mu-event",
                  json.dumps({"event": "asr", "task": {**tarea, "path": ""}}))
        h.wait_property("user-data/mu/subs", lambda s: bool(s) and s.get("status") == "done", timeout=20)

        pedir_transcripcion(h)
        st = h.wait_property("user-data/mu/chapters",
                             lambda s: bool(s) and (s.get("transcript") or {}).get("status") == "done", timeout=60)
        t = st["transcript"]
        assert t["source"] == "ai" and t["lines"] >= 1 and t["chars"] > 0, t
        assert "Transcripción lista" in st["osd"], st["osd"]
        assert Path(t["out"]).is_file() and Path(t["out"]).parent == video.parent, t["out"]

        h.command("keypress", "ctrl+alt+c")           # el paso siguiente del flujo: copiarla para la IA
        osd_de(h, "Copiado: ")
        assert "TRANSCRIPCIÓN:" in leer_portapapeles(h)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
