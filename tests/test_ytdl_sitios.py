"""H37/D4 · lo que decimos de Instagram y TikTok tiene que ser verdad en el yt-dlp instalado.

`mpvd/ytdl/sites.py` reconoce las formas de URL con expresiones propias (barato: ni red ni importar yt_dlp). Este test
comprueba esa tabla contra el binario de verdad, preguntando a cada extractor con su propio `suitable()`: si una versión
nueva de yt-dlp añade el extractor de «guardados», el test falla y la tabla se actualiza en vez de seguir mintiendo.
"""

from __future__ import annotations

import sys

import pytest

from pathlib import Path

from mpvd.ytdl.binary import find_ytdlp
from mpvd.ytdl.sites import SHAPES, broken_extractors, describe, shape_for

CASOS = {
    "https://www.instagram.com/sc.tecnico/saved/all-posts/": "instagram_saved",
    "https://www.instagram.com/sc.tecnico/": "instagram_profile",
    "https://www.tiktok.com/@alguien": "tiktok_profile",
    "https://www.tiktok.com/@alguien/collection/lo-mio-7312": "tiktok_collection",
    "https://www.tiktok.com/favorite": "tiktok_favorites",
    "https://www.youtube.com/watch?v=aqz-KE-bpKQ": None,
    "https://www.instagram.com/p/Cxyz/": None,              # un vídeo suelto: eso siempre se intenta
}


def test_las_formas_se_reconocen_y_lo_normal_no_estorba():
    for url, esperado in CASOS.items():
        shape = shape_for(url)
        assert (shape.id if shape else None) == esperado, url
    # los guardados se rechazan CON alternativa: decir «no se puede» y nada más no sirve de nada
    for shape in SHAPES:
        if shape.extractor is None:
            assert shape.alternative, shape.id
    d = describe("https://www.instagram.com/sc.tecnico/saved/all-posts/")
    assert d["supported"] is False and "guardados" in d["message"] and d["alternative"]
    assert describe("https://www.youtube.com/watch?v=x")["supported"] is True
    # un extractor roto se sigue ofreciendo, pero avisando
    d = describe("https://www.instagram.com/sc.tecnico/", {"instagram:user"})
    assert d["supported"] is True and "roto" in d["warning"] and d["private"] is True
    assert describe("https://www.instagram.com/sc.tecnico/", set())["warning"] == ""


def _extractores_para(url: str, clases) -> list[str]:
    return [c.IE_NAME for c in clases if c.IE_NAME != "generic" and c.suitable(url)]


def test_contra_el_ytdlp_instalado_los_extractores_son_los_que_decimos():
    binary = find_ytdlp(Path(__file__).resolve().parent.parent)
    if binary is None:
        pytest.skip("sin yt-dlp instalado (tools/vendor.sh)")
    sys.path.insert(0, str(binary.path))
    try:
        from yt_dlp.extractor import gen_extractor_classes
    except ImportError:
        pytest.skip("el yt-dlp instalado no es el zipimport oficial: no se puede preguntar a sus extractores")
    clases = list(gen_extractor_classes())
    for url, esperado in CASOS.items():
        acepta = _extractores_para(url, clases)
        shape = shape_for(url)
        if shape is None:
            continue
        if shape.extractor is None:
            assert acepta == [], f"{url}: ya hay extractor ({acepta}): actualiza SHAPES en mpvd/ytdl/sites.py"
        else:
            assert shape.extractor in acepta, f"{url}: lo acepta {acepta}, no «{shape.extractor}»"
    # y los «CURRENTLY BROKEN» se leen del propio binario, no de una lista nuestra
    rotos = broken_extractors(binary)
    assert "instagram:user" in rotos or "instagram:user" not in [c.IE_NAME for c in clases], \
        "si instagram:user ya no está roto, quita el aviso"
