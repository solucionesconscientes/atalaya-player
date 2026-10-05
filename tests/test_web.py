"""H50 · la página pública: que diga la verdad y que no traiga nada de fuera.

Lo que se vigila es lo que el propio diseño (docs/SITIO-WEB.md) dice que hay que cumplir y lo que sería
vergonzoso que fallara justo en la página que predica privacidad: ni un script, ni una fuente, ni un píxel que
venga de otro servidor, ni cookies, ni analítica. Y lo que es peor que un fallo técnico: anunciar una descarga
con una suma SHA-256 que no es la del fichero, o decir que algo está probado cuando no lo está.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from tests.conftest import ROOT

WEB = ROOT / "web"
IDIOMAS = ("es", "en", "fr")


def cargar_constructor():
    spec = importlib.util.spec_from_file_location("build_web", ROOT / "tools/build_web.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def construida(tmp_path_factory) -> Path:
    """Se construye en un sitio aparte: así el test no depende de que `web/` esté al día ni lo pisa."""
    salida = tmp_path_factory.mktemp("web")
    subprocess.run(["python3", str(ROOT / "tools/build_web.py"), "--out", str(salida)],
                   cwd=ROOT, check=True, capture_output=True)
    return salida


def paginas(salida: Path) -> dict[str, str]:
    return {"es": (salida / "index.html").read_text(encoding="utf-8"),
            "en": (salida / "en/index.html").read_text(encoding="utf-8"),
            "fr": (salida / "fr/index.html").read_text(encoding="utf-8")}


def test_el_texto_esta_en_los_tres_idiomas_con_las_mismas_claves():
    """Escribir tres HTML a mano garantiza que en un mes digan cosas distintas: hay una sola fuente y aquí se
    comprueba que no se le ha olvidado nada a ningún idioma."""
    contenido = json.loads((WEB / "contenido.json").read_text(encoding="utf-8"))
    claves = {lang: set(contenido[lang]) for lang in IDIOMAS}
    assert claves["es"] == claves["en"] == claves["fr"], {k: claves["es"] ^ v for k, v in claves.items()}
    for lang in IDIOMAS:
        assert len(contenido[lang]["cosas"]) == 5, "las cinco cosas del diseño, ni cuatro ni seis"
        assert len(contenido[lang]["estado_tabla"]) == 4
        for cosa in contenido[lang]["cosas"]:
            assert set(cosa) == {"titulo", "texto", "captura", "alt"}
            assert cosa["alt"], "una captura sin descripción no se puede leer en voz alta ni deja hueco decente"


def test_las_tres_paginas_se_construyen_y_se_enlazan(construida):
    p = paginas(construida)
    for lang, texto in p.items():
        assert f'<html lang="{lang}"' in texto
        assert "Atalaya" in texto and "</html>" in texto
        # el selector de idioma lleva a los otros dos y se marca a sí mismo
        assert 'aria-current="true"' in texto
        for otro in IDIOMAS:
            destino = "index.html" if otro == "es" else f"{otro}/index.html"
            assert destino in texto, f"{lang} no enlaza a {otro}"


def test_no_trae_nada_de_fuera_ni_cookies_ni_analitica(construida):
    """La página de un programa que dice «nada sale de tu ordenador» no puede cargar una fuente de Google.

    Se miran los comentarios aparte: el propio HTML lleva escrito «ni cookies, ni analítica», que es justo lo que
    se busca prohibir, y hacer saltar el test con eso sería una broma."""
    for lang, texto in paginas(construida).items():
        sin_comentarios = re.sub(r"<!--.*?-->", "", texto, flags=re.S).lower()
        assert "<script" not in sin_comentarios, f"{lang}: ni un script"
        assert "document.cookie" not in sin_comentarios and "set-cookie" not in sin_comentarios
        for sospechoso in ("googleapis", "gstatic", "google-analytics", "googletagmanager", "cdn.", "unpkg",
                           "jsdelivr", "fonts.g", "facebook", "doubleclick", "gtag(", "matomo", "plausible"):
            assert sospechoso not in sin_comentarios, f"{lang}: {sospechoso}"
        # ni una URL absoluta a otro dominio en los recursos (href/src): todo relativo
        for atributo in re.findall(r'(?:src|href)="([^"]+)"', texto):
            if atributo.startswith("#") or atributo.startswith("http") is False:
                continue
            assert atributo.startswith("https://solucionesconscientes.es"), atributo


def test_lo_que_se_anuncia_para_descargar_es_de_verdad(construida):
    """Una suma que no es la del fichero es peor que no publicar ninguna, y una descarga que no existe es un 404
    en la cara de quien venía a probarlo: solo se anuncia lo que está construido, con su tamaño y su suma."""
    mod = cargar_constructor()
    version = tomllib.load(open(ROOT / "pyproject.toml", "rb"))["project"]["version"]
    lista = mod.paquetes(ROOT / "dist", version)
    texto = (construida / "index.html").read_text(encoding="utf-8")
    if not lista:
        pytest.skip("sin paquetes en dist/: nada que anunciar")
    for p in lista:
        assert p["name"] in texto
        assert p["sha256"] in texto
        real = hashlib.sha256((ROOT / "dist" / p["name"]).read_bytes()).hexdigest()
        assert p["sha256"] == real, p["name"]
    # y el latest.json que consulta el programa sale de los mismos datos
    latest = json.loads((construida / "latest.json").read_text(encoding="utf-8"))
    assert latest["version"] == version
    assert {f["name"] for f in latest["files"]} == {p["name"] for p in lista}
    assert all(len(f["sha256"]) == 64 for f in latest["files"])


def test_lo_que_no_esta_probado_se_dice_en_la_pagina(construida):
    """El diseño lo pide expresamente: decirlo en la página pública, no esconderlo, porque quien se lo encuentre
    luego se va. Si alguien quita esa fila, este test lo dice."""
    textos = paginas(construida)
    assert "sin ejecutar en una máquina ARM" in textos["es"]
    assert "sin abrir en un Windows de verdad" in textos["es"]
    assert "not yet run on an ARM machine" in textos["en"]
    assert "pas encore exécutés sur une machine ARM" in textos["fr"]


def test_una_captura_que_falta_deja_su_hueco_y_no_una_maqueta(construida):
    """Mientras no haya capturas de verdad, la página enseña el hueco con su descripción: se puede publicar hoy y
    se ve qué falta. Lo que no se hace nunca es inventarse una maqueta y llamarla captura."""
    texto = (construida / "index.html").read_text(encoding="utf-8")
    contenido = json.loads((WEB / "contenido.json").read_text(encoding="utf-8"))
    for cosa in contenido["es"]["cosas"]:
        if (WEB / cosa["captura"]).is_file():
            assert cosa["captura"] in texto
        else:
            assert cosa["alt"] in texto and "sin-captura" in texto


def test_la_pagina_de_arquitectura_sale_del_documento_que_ya_existe(construida):
    """No se escribe dos veces: la página de «cómo está hecho» se genera de docs/ARQUITECTURA.md."""
    pagina = (construida / "arquitectura.html").read_text(encoding="utf-8")
    fuente = (ROOT / "docs/ARQUITECTURA.md").read_text(encoding="utf-8")
    assert "<h1>" in pagina and "Atalaya" in pagina
    assert "<script" not in pagina.lower()
    for trozo in ("No es un fork de mpv", "JSON-RPC", "whisper.cpp"):
        assert trozo in fuente and trozo in pagina
    # las tablas del documento salen como tablas, no como texto con barras
    assert "<table>" in pagina and "|---" not in pagina


def test_el_estilo_usa_los_colores_de_la_marca():
    css = (WEB / "estilo.css").read_text(encoding="utf-8")
    marca = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))["colors"]
    for nombre, color in marca.items():
        assert color.lower() in css.lower(), f"falta el color {nombre} ({color})"


def test_las_capturas_se_hacen_con_el_programa_recien_estrenado():
    """La primera tanda de capturas salió con el menú enseñando el historial de quien tiene este equipo —los
    últimos vídeos vistos, con sus títulos— camino de una página pública. Las capturas se hacen con una carpeta
    de datos vacía, y esto lo vigila: si alguien quita esa línea, lo siguiente que se publica es el historial de
    alguien."""
    guion = (ROOT / "tools/capturas.sh").read_text(encoding="utf-8")
    assert "MPV_UOS_DATA_DIR" in guion and "rm -rf \"$DATOS\"" in guion
    assert "export MPV_UOS_DATA_DIR" in guion
    # y el porqué, escrito donde se lee
    assert "historial" in guion


def test_ninguna_captura_lleva_nombres_de_archivos_de_nadie():
    """Comprobación de verdad sobre los píxeles no se puede hacer aquí, pero sí sobre lo que se publica: si
    alguna captura se hiciera a mano con la carpeta de datos real, lo normal es que pese bastante más que las
    hechas con el vídeo de pruebas. Esto no lo detecta todo; lo que lo detecta es la de arriba."""
    capturas = sorted((ROOT / "web/capturas").glob("*.png")) if (ROOT / "web/capturas").is_dir() else []
    if not capturas:
        pytest.skip("sin capturas (tools/capturas.sh)")
    assert all(c.stat().st_size < 4 * 1024 * 1024 for c in capturas), [c.name for c in capturas]
