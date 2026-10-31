"""F2 · capítulos: leerlos del ``-J``, elegirlos, entregarlos y unirlos sin recodificar.

Lo que se comprueba aquí es lo que se le promete a quien usa el reproductor: que la lista de capítulos diga lo que
dura cada uno, que una selección vacía o imposible falle con un motivo legible (en vez de bajar un fichero de cero
bytes), que la orden de yt-dlp lleve ``--split-chapters`` con su plantilla ``chapter:`` cuando van todos y un
``--download-sections`` por capítulo cuando van algunos, y que unir dos trozos **no** recodifique ni mienta con la
duración: dos senos de 2 s tienen que dar un fichero de 4 s.

Los trozos de la unión se generan con ffmpeg en el directorio temporal: sin red y sin depender de nada descargado.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from mpvd.ytdl import chapters
from mpvd.ytdl.chapters import Union, args_capitulos, capitulos, nombre_unido, plantilla_capitulos, seleccionar, unir
from mpvd.ytdl.presets import DownloadSpec, build_args

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

# Un -J reducido a los capítulos: ocho bloques de una clase, con dos títulos repetidos a propósito («Parte 1» y
# «Parte 10» se parecen, y «Prácticas» está dos veces) para poder comprobar que la selección por título no se lleva
# de más y que un título repetido no sirve para elegir.
INFO = {
    "id": "aqz-KE-bpKQ", "title": "Clase de física", "duration": 600,
    "chapters": [
        {"start_time": 0, "end_time": 30, "title": "Intro"},
        {"start_time": 30, "end_time": 120, "title": "Parte 1"},
        {"start_time": 120, "end_time": 200, "title": "Parte 10"},
        {"start_time": 200, "end_time": 260, "title": "Prácticas"},
        {"start_time": 260, "end_time": 330, "title": "Entrevista con la profesora"},
        {"start_time": 330, "end_time": 400, "title": "Prácticas"},
        {"start_time": 400, "end_time": 500, "title": "Dudas"},
        {"start_time": 500, "end_time": 600, "title": "Cierre"},
    ],
}


def _numeros(caps):
    return [c["numero"] for c in caps]


# ------------------------------------------------------------------ leer el -J

def test_ocho_capitulos_con_numero_titulo_inicio_y_duracion():
    caps = capitulos(INFO)
    assert len(caps) == 8
    assert caps[0] == {"numero": 1, "titulo": "Intro", "inicio": 0.0, "fin": 30.0, "duracion": 30.0}
    assert caps[4]["titulo"] == "Entrevista con la profesora"
    assert caps[4]["duracion"] == 70.0
    assert [c["numero"] for c in caps] == list(range(1, 9))


def test_un_video_sin_capitulos_no_es_un_error():
    assert capitulos({"id": "x", "duration": 100}) == []
    assert capitulos({}) == [] and capitulos(None) == []      # type: ignore[arg-type]


def test_un_capitulo_sin_fin_llega_hasta_el_final_y_sin_titulo_se_numera():
    caps = capitulos({"duration": 300, "chapters": [{"start_time": 100, "title": ""},
                                                    {"start_time": 200, "end_time": 250, "title": "Dos"}]})
    assert caps[0]["fin"] == 300.0 and caps[0]["duracion"] == 200.0
    assert caps[0]["titulo"] == "Capítulo 1" and caps[1]["titulo"] == "Dos"


# ------------------------------------------------------------------ seleccionar

@pytest.mark.parametrize(("expr", "esperado"), [
    ("3", [3]),                       # uno suelto
    ("3-5", [3, 4, 5]),               # rango
    ("2,4,7", [2, 4, 7]),             # varios sueltos
    ("3-", [3, 4, 5, 6, 7, 8]),       # abierto por detrás: del 3 al final
    ("-4", [1, 2, 3, 4]),             # abierto por delante: del principio al 4
    ("7,2-3,2", [2, 3, 7]),           # ordenada y sin repetir
    ([4, 1], [1, 4]),                 # la lista que manda la pantalla
    ("/entrevista/", [5]),            # regex entre barras
    ("ENTREVISTA", [5]),              # una palabra: se busca en el título, sin mayúsculas
    ("intro", [1]),
    ("parte 1", [2, 3]),              # el título se busca como texto: «Parte 1» aparece dentro de «Parte 10»
])
def test_seleccion_por_indices_rangos_abiertos_y_titulo(expr, esperado):
    assert _numeros(seleccionar(capitulos(INFO), expr)) == esperado


def test_una_seleccion_vacia_falla_con_el_motivo():
    """Un cero capítulos es un fichero de cero bytes: se para antes, y se dice por qué."""
    for expr in ("", "   ", None, []):
        with pytest.raises(ValueError, match="capítulo"):
            seleccionar(capitulos(INFO), expr)


def test_un_capitulo_que_no_existe_se_nombra():
    with pytest.raises(ValueError, match="no existe el 9"):
        seleccionar(capitulos(INFO), "9")
    with pytest.raises(ValueError, match="no existe el 9"):
        seleccionar(capitulos(INFO), [1, 9])


def test_un_rango_al_reves_o_un_texto_raro_se_explican():
    with pytest.raises(ValueError, match="va al revés"):
        seleccionar(capitulos(INFO), "5-2")
    with pytest.raises(ValueError, match="no entiendo"):
        seleccionar(capitulos(INFO), "-")
    with pytest.raises(ValueError, match="se llama así"):
        seleccionar(capitulos(INFO), "/no existe/")


def test_sin_capitulos_la_seleccion_no_inventa_nada():
    with pytest.raises(ValueError, match="no tiene capítulos"):
        seleccionar([], "1")


# ------------------------------------------------------------------ la orden de yt-dlp

def test_todos_los_capitulos_van_con_split_chapters_y_plantilla_chapter():
    args = args_capitulos(capitulos(INFO), chapters.SEPARADO, None, todos=capitulos(INFO))
    assert args[:1] == ["--split-chapters"] and "--download-sections" not in args
    plantilla = args[args.index("-o") + 1]
    assert plantilla.startswith("chapter:") and "%(section_number)02d" in plantilla
    assert "%(section_title)s" in plantilla and plantilla.endswith(".%(ext)s")
    # la plantilla por defecto es la del diseño, tal cual
    assert plantilla == chapters.PLANTILLA


def test_un_subconjunto_pide_un_trozo_por_capitulo_y_no_parte_los_demas():
    """Con --split-chapters saldrían también los capítulos que no se han elegido: se pide trozo por elegido."""
    todos = capitulos(INFO)
    args = args_capitulos([todos[1], todos[4]], chapters.SEPARADO, None, todos=todos)
    assert "--split-chapters" not in args
    assert args.count("--download-sections") == 2
    assert args[args.index("--download-sections") + 1] == "^Parte\\ 1$"   # anclado: «Parte 1» no «Parte 10»


def test_un_solo_fichero_repite_download_sections_y_avisa_de_la_union():
    todos = capitulos(INFO)
    args = args_capitulos(todos[:3], chapters.UNIDO, None, todos=todos)
    assert args.count("--download-sections") == 3 and "--split-chapters" not in args
    assert "se unen después" in chapters.NOTA_UNION and "sin recodificar" in chapters.NOTA_UNION


def test_un_titulo_repetido_no_sirve_para_elegir_y_se_usa_el_tiempo():
    todos = capitulos(INFO)
    args = args_capitulos([todos[3], todos[5]], chapters.UNIDO, None, todos=todos)
    rangos = [args[i + 1] for i, a in enumerate(args) if a == "--download-sections"]
    assert rangos == ["*200.000-260.000", "*330.000-400.000"]


def test_plantilla_propia_sin_titulo_se_rechaza_y_con_titulo_se_prefija():
    with pytest.raises(ValueError, match="section_title"):
        plantilla_capitulos("%(title)s.%(ext)s")
    assert plantilla_capitulos("%(section_number)02d %(section_title)s.%(ext)s").startswith("chapter:")
    assert plantilla_capitulos("chapter:%(section_title)s").endswith(".%(ext)s")


def test_modo_desconocido_y_seleccion_vacia_en_los_argumentos():
    with pytest.raises(ValueError, match="modo"):
        args_capitulos(capitulos(INFO), "otro", None, todos=capitulos(INFO))
    with pytest.raises(ValueError, match="capítulo"):
        args_capitulos([], chapters.SEPARADO, None, todos=capitulos(INFO))


# ------------------------------------------------------------------ build_args con capítulos

def test_build_args_pide_los_capitulos_y_su_plantilla():
    todos = capitulos(INFO)
    spec = DownloadSpec(url=URL, kind="video", perfil="compatible", chapters=todos, extra={"chapters_all": todos})
    args = build_args(spec, "/out")
    assert "--split-chapters" in args
    assert args[args.index("-o") + 1] == chapters.PLANTILLA
    # el fichero de cada capítulo va a la carpeta de descargas que se pidió
    assert args[args.index("-P") + 1] == "/out"


def test_build_args_con_algunos_capitulos_nombra_cada_trozo_por_su_tiempo():
    todos = capitulos(INFO)
    spec = DownloadSpec(url=URL, kind="video", perfil="compatible", chapters=todos[:2],
                        chapters_mode="unido", extra={"chapters_all": todos})
    args = build_args(spec, "/out")
    assert args.count("--download-sections") == 2
    assert args[args.index("-o") + 1].endswith(" [%(section_start)d-%(section_end)d s].%(ext)s")


def test_build_args_sin_el_analisis_no_supone_que_son_todos():
    spec = DownloadSpec(url=URL, kind="video", chapters=[1, 3])
    with pytest.raises(ValueError, match="ytdl.chapters"):
        build_args(spec, "/out")


# ------------------------------------------------------------------ unir sin recodificar

@pytest.fixture(scope="module")
def trozos(tmp_path_factory: pytest.TempPathFactory):
    """Dos (y tres) trozos de 2 s generados con ffmpeg: AAC en .m4a y un PCM en .wav para el caso que no se puede unir."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("sin ffmpeg/ffprobe no se puede probar la unión")
    d = tmp_path_factory.mktemp("trozos")
    hechos = {}
    for nombre, dur, codec, ext in (("a", 2, "aac", "m4a"), ("b", 2, "aac", "m4a"), ("c", 2, "pcm_s16le", "wav")):
        p = d / f"{nombre}.{ext}"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={dur}", "-c:a", codec, str(p)], check=True, timeout=120)
        hechos[nombre] = p
    return hechos


def test_dos_trozos_se_unen_sin_recodificar_y_duran_la_suma(trozos):
    salida = trozos["a"].with_name("unido.m4a")
    res = unir([trozos["a"], trozos["b"]], salida)
    assert isinstance(res, Union) and res.ok and res.error is None
    assert salida.is_file() and salida.stat().st_size > 0
    assert res.esperado == pytest.approx(4.0, abs=0.05)
    assert res.duracion == pytest.approx(4.0, abs=1.0)          # ±1 s, como se promete
    # sin recodificar: el códec del origen sigue siendo el mismo
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", str(salida)], capture_output=True, text=True, check=True).stdout.strip()
    assert codec == "aac"


def test_dos_trozos_de_codec_distinto_no_se_unen_y_lo_dicen(trozos):
    salida = trozos["a"].with_name("imposible.m4a")
    res = unir([trozos["a"], trozos["c"]], salida)
    assert res.ok is False and res.error and "sin recodificar" in res.error
    assert "pcm_s16le" in res.error and "aac" in res.error       # dice qué no coincide, no «error»
    assert not salida.exists() or salida.stat().st_size == 0     # no deja un fichero roto con el nombre bueno


def test_unir_necesita_dos_trozos_y_que_esten(trozos):
    with pytest.raises(ValueError, match="dos trozos"):
        unir([trozos["a"]], trozos["a"].with_name("x.m4a"))
    with pytest.raises(ValueError, match="falta el trozo"):
        unir([trozos["a"], trozos["a"].with_name("no-existe.m4a")], trozos["a"].with_name("x.m4a"))


def test_el_nombre_del_unido_quita_el_tramo_del_corte():
    assert nombre_unido(["/x/Clase [120-200 s].mkv", "/x/Clase [200-260 s].mkv"]) == "Clase.mkv"
    assert nombre_unido(["/x/Clase.mkv"]) == "Clase.mkv"
