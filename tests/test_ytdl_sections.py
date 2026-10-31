"""F2 · fragmentos de tiempo: rangos, margen de cola, orden de yt-dlp y aviso de precisión.

Lo que se fija aquí es lo que el diseño pide (docs/DISENO-GESTOR-DESCARGAS.md §5): varios rangos en una sola pasada
—un ``--download-sections`` por rango—, tiempos negativos contados desde el final, 0,3 s de cola para no cortar la
última sílaba, y el aviso medido de que con VP9/Opus el corte por copia se va (16 s donde se pidieron 6) mientras
que con H.264+AAC va a ±0,03 s.
"""

from __future__ import annotations

import math

import pytest

from mpvd.ytdl import sections
from mpvd.ytdl.presets import DownloadSpec, build_args
from mpvd.ytdl.sections import MARGEN_COLA, Rango, args_rangos, parsear, precision, total


# ------------------------------------------------------------------ parsear

def test_un_rango_simple_sale_con_el_margen_de_cola():
    (r,) = parsear("00:10-02:30")
    assert (r.inicio, r.fin, r.cola) == (10.0, 150.0, MARGEN_COLA)
    assert r.fin_con_cola() == pytest.approx(150.3)
    assert r.texto() == "*10.000-150.300"


def test_horas_minutos_y_segundos():
    (r,) = parsear("1:00:00-1:05:00")
    assert (r.inicio, r.fin) == (3600.0, 3900.0)
    assert r.duracion() == pytest.approx(300.3)


def test_los_ultimos_treinta_segundos_desde_el_final():
    """``-00:30-inf``: el principio negativo cuenta desde el final y ``inf`` es el final del vídeo."""
    (r,) = parsear("-00:30-inf")
    assert r.inicio == -30.0 and math.isinf(r.fin)
    assert r.texto() == "*-30.000-inf"
    assert r.duracion(600) == pytest.approx(30.3)
    assert r.absoluto(600) == (570.0, 600.0)


def test_rangos_abiertos_por_delante_y_por_detras():
    (a,) = parsear("00:10-")
    assert a.fin == math.inf and a.texto() == "*10.000-inf"
    (b,) = parsear("-02:30")
    assert (b.inicio, b.fin) == (0.0, 150.0)


def test_varios_rangos_de_una_vez_con_coma_o_salto_de_linea():
    rs = parsear("00:10-02:30, 1:00:00-1:05:00\n00:05-00:06")
    assert [r.texto() for r in rs] == ["*10.000-150.300", "*3600.000-3900.300", "*5.000-6.300"]
    assert [r.texto() for r in parsear(["00:10-02:30", "00:20-00:30"])] == ["*10.000-150.300", "*20.000-30.300"]


def test_el_margen_se_puede_quitar_y_entonces_el_rango_va_tal_cual():
    """«Grabar» de un vídeo de internet pide el tramo exacto: sin margen no se le reescribe el número."""
    (r,) = parsear("*12.50-20", 0)
    assert r.texto() == "*12.50-20"
    (q,) = parsear("00:10-02:30", 1.5)
    assert q.texto() == "*10.000-151.500"


@pytest.mark.parametrize("malo", [
    "10:20",          # un tiempo suelto no es un rango
    "a-b",
    "20-10",          # el final antes que el principio
    "-00:30-20:00",   # mezcla «desde el final» con «desde el principio»
    "02:30-00:10",
])
def test_un_rango_imposible_se_explica(malo):
    with pytest.raises(ValueError):
        parsear(malo)


def test_sin_expresion_no_hay_rangos():
    assert parsear(None) == [] and parsear("") == [] and parsear([]) == []


# ------------------------------------------------------------------ la orden de yt-dlp

def test_un_download_sections_por_rango_y_en_orden():
    args = args_rangos(parsear("00:03-00:05,00:07-00:09"))
    assert args == ["--download-sections", "*3.000-5.300", "--download-sections", "*7.000-9.300"]


def test_la_ficha_del_rango_resuelve_los_negativos_con_la_duracion():
    (r,) = parsear("-00:00:10-inf")
    ficha = r.to_dict(300)
    assert ficha["inicio"] == -10.0 and ficha["duracion"] == pytest.approx(10.3)
    assert ficha["texto"] == "*-10.000-inf"
    assert Rango(3, 9).to_dict()["duracion"] == pytest.approx(6.3)


def test_el_total_suma_los_fragmentos():
    assert total(parsear("00:10-00:20,00:30-00:40")) == pytest.approx(20.6)
    assert total(parsear("-00:30-inf"), 600) == pytest.approx(30.3)


def test_build_args_pide_los_dos_trozos_y_un_nombre_por_tramo():
    spec = DownloadSpec(url="https://x/y", kind="video", perfil="compatible",
                        sections="00:03-00:05,00:07-00:09", sections_margin=0.3)
    args = build_args(spec, "/out")
    assert args.count("--download-sections") == 2
    assert args[args.index("-o") + 1].endswith(" [%(section_start)d-%(section_end)d s].%(ext)s")
    # los fragmentos piden H.264 + AAC aunque el perfil sea otro: es lo único que corta exacto
    assert args[args.index("-f") + 1] == "bv*[vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
    # unir es un paso de después, no un argumento: con la unión pedida la orden es la misma
    spec.sections_join = True
    assert build_args(spec, "/out").count("--download-sections") == 2


# ------------------------------------------------------------------ precisión del corte

def test_el_perfil_de_los_fragmentos_es_el_compatible():
    assert sections.PERFIL_FRAGMENTOS == "compatible"


def test_con_h264_y_aac_el_corte_es_exacto():
    aviso = precision("avc1.640028", "mp4a.40.2")
    assert "±0,03 s" in aviso and "H.264" in aviso
    assert sections.es_exacto("avc1.4d401f", "mp4a.40.2") is True
    assert sections.es_exacto("h264", "aac") is True


def test_con_vp9_y_opus_se_avisa_de_lo_que_se_va():
    aviso = precision("vp9", "opus")
    assert "16 s" in aviso and "6" in aviso and "H.264" in aviso
    assert sections.es_exacto("vp09.00.10.08", "opus") is False
    assert sections.es_exacto("av01.0.04M.08", "opus") is False


def test_con_h264_y_opus_tambien_se_avisa():
    """La imagen se corta bien, pero el audio Opus arrastra: se dice, no se promete exactitud."""
    assert sections.es_exacto("avc1.640028", "opus") is False
    assert "Opus" in precision("avc1.640028", "opus")


def test_sin_datos_del_codigo_no_se_promete_nada():
    assert sections.es_exacto("", "") is True          # sin códec declarado no hay nada que avisar
    assert "fotograma clave" in precision("", "ac3")
