"""El audio que se baja por defecto, las pistas del origen marcadas a mano y la «extensión compatible».

Tres reglas del producto, cada una con su prueba:

* **el audio por defecto es el original y el pequeño**: de los Opus del origen, el de menor bitrate; sin Opus, el
  AAC de menor bitrate y la ficha lo dice; sin ninguno de los dos, la mejor pista y el aviso de que para
  ``.opus``/``.m4a`` habría que convertir. Nunca se recodifica en el camino de descarga;
* **se pueden marcar varias pistas** (dos idiomas de audio, por ejemplo): se muxean tal cual en un MKV;
* **«extensión compatible»** entrega un Opus como ``.ogg``: es un renombrado, así que el fichero pesa lo mismo y
  tiene el mismo contenido byte a byte.
"""

from __future__ import annotations

import asyncio

from pathlib import Path

import pytest

from mpvd.ytdl import policy
from mpvd.ytdl.downloads import extension_compatible
from mpvd.ytdl.policy import plan
from mpvd.ytdl.presets import DownloadSpec, build_args
from tests.test_ytdl_plan_rpc import plan_env, with_server  # noqa: F401 - fixtures

URL = "https://example.test/v"
CLIP = "https://fake.test/clip"

# Dos Opus (160k y 64k) y un AAC: el defecto tiene que ser el Opus de 64k, no el de 160k ni el AAC.
CON_OPUS = {
    "id": "x", "title": "Charla", "duration": 600, "categories": ["Education"],
    "formats": [
        {"format_id": "303", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 720, "fps": 30,
         "tbr": 1500, "filesize": 112500000},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160, "audio_channels": 2},
        {"format_id": "249", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 64, "audio_channels": 2},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128,
         "audio_channels": 2, "language": "es"},
    ],
}
# El mismo origen sin Opus: solo AAC, en dos bitrates y dos idiomas
SOLO_AAC = {
    "id": "y", "title": "Charla doblada", "duration": 600,
    "formats": [
        {"format_id": "303", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "tbr": 1500},
        {"format_id": "141", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 256,
         "audio_channels": 2, "language": "es"},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 96,
         "audio_channels": 2, "language": "en"},
    ],
}
# Ni Opus ni AAC: aquí no se recodifica nada por detrás, se avisa
NI_UNO_NI_OTRO = {
    "id": "z", "title": "Conferencia vieja", "duration": 300,
    "formats": [
        {"format_id": "mp3-64", "ext": "mp3", "vcodec": "none", "acodec": "mp3", "abr": 64},
        {"format_id": "mp3-128", "ext": "mp3", "vcodec": "none", "acodec": "mp3", "abr": 128},
    ],
}


# ------------------------------------------------------------------ 1 · el audio por defecto

def test_el_trabajo_por_defecto_lleva_el_opus_de_menor_bitrate():
    """Con Opus 160k y Opus 64k en el origen, lo que se baja es el de 64k: el original y el pequeño."""
    d = plan(CON_OPUS, "opus_origen")
    assert d.audio_id == "249" and d.video_id is None
    assert d.contenedor == "opus" and d.recodificar is False
    assert "Opus" in d.nota and "64k" in d.nota and "menor bitrate" in d.nota
    assert policy.AVISO_SIN_OPUS not in d.nota        # hay Opus: no hay nada que avisar
    # y el vídeo por defecto se lleva ese mismo audio, sin recodificar
    v = plan(CON_OPUS, "video_720")
    assert (v.video_id, v.audio_id, v.contenedor, v.recodificar) == ("303", "249", "mkv", False)
    # la orden que sale de esa decisión pide esa pista exacta y no convierte nada
    argv = build_args(DownloadSpec(url=URL, kind="audio_original", perfil="opus_origen", audio_id=d.audio_id),
                      "/tmp/out")
    assert argv[argv.index("-f") + 1] == "249" and "-x" in argv
    assert "--audio-format" not in argv and "--recode-video" not in argv


def test_sin_opus_se_entrega_el_aac_mas_bajo_y_la_ficha_lo_dice():
    d = plan(SOLO_AAC, "opus_origen")
    assert d.audio_id == "140" and d.contenedor == "m4a" and d.recodificar is False
    assert "AAC" in d.nota and "96k" in d.nota
    assert policy.AVISO_SIN_OPUS in d.nota and ".m4a" in d.nota


def test_sin_opus_ni_aac_se_coge_la_mejor_y_se_avisa_en_vez_de_recodificar():
    d = plan(NI_UNO_NI_OTRO, "opus_origen")
    assert d.audio_id == "mp3-128" and d.recodificar is False        # la mejor que hay, copiada tal cual
    assert policy.AVISO_SIN_OPUS_NI_AAC in d.nota and "convertirlo" in d.nota


# ------------------------------------------------------------------ 2 · varias pistas del origen

def test_varias_pistas_marcadas_se_muxean_tal_cual_en_un_mkv():
    d = plan(SOLO_AAC, "pistas", "303", ["141", "140"])
    assert d.video_id == "303" and d.audio_id == ["141", "140"]
    assert d.contenedor == "mkv" and d.recodificar is False          # H.264 + AAC cabría en MP4; dos audios, no
    assert "MKV" in d.nota and "copiado tal cual" in d.nota
    argv = build_args(DownloadSpec(url=URL, kind="video", perfil="pistas", video_id="303",
                                   audio_id=["141", "140"], container="mkv"), "/tmp/out")
    assert argv[argv.index("-f") + 1] == "303+141+140"
    assert argv[argv.index("--merge-output-format") + 1] == "mkv"
    # sin esto yt-dlp se queda con el primer audio y tira el resto sin decir nada
    assert "--audio-multistreams" in argv and "--video-multistreams" not in argv
    assert "--recode-video" not in argv and "-x" not in argv
    # y con una sola pista de cada clase no hace falta (es el caso de siempre)
    una = build_args(DownloadSpec(url=URL, kind="video", perfil="pistas", video_id="303", audio_id="140",
                                  container="mp4"), "/tmp/out")
    assert "--audio-multistreams" not in una


def test_dos_audios_sin_video_salen_en_un_solo_fichero_con_los_dos():
    """Sin vídeo y con dos audios no se puede «extraer el audio»: ExtractAudio se quedaría con el primero."""
    d = plan(SOLO_AAC, "pistas", None, ["141", "140"])
    assert d.video_id is None and d.audio_id == ["141", "140"] and d.contenedor == "mkv"
    argv = build_args(DownloadSpec(url=URL, kind="audio_original", perfil="pistas", audio_id=["141", "140"],
                                   container=d.contenedor), "/tmp/out")
    assert argv[argv.index("-f") + 1] == "141+140" and "--audio-multistreams" in argv
    assert "-x" not in argv and argv[argv.index("--merge-output-format") + 1] == "mkv"
    # y una sola pista de audio sigue saliendo suelta, con la extensión de su códec
    sola = plan(SOLO_AAC, "pistas", None, "140")
    assert sola.contenedor == "m4a" and "-x" in build_args(
        DownloadSpec(url=URL, kind="audio_original", perfil="pistas", audio_id="140",
                     container=sola.contenedor), "/tmp/out")
    # una sola pareja H.264 + AAC sí es MP4, y con capítulos dentro vuelve a ser MKV (es el único que los lleva)
    assert plan(SOLO_AAC, "pistas", "303", "140").contenedor == "mp4"
    assert plan(SOLO_AAC, "pistas", "303", "140", capitulos=True).contenedor == "mkv"


def test_la_lista_de_pistas_trae_todos_los_flujos_con_su_tamano():
    p = policy.pistas(CON_OPUS)
    assert [f["id"] for f in p["video"]] == ["303"]
    assert sorted(f["id"] for f in p["audio"]) == ["140", "249", "251"]
    declarado = next(f for f in p["video"] if f["id"] == "303")
    assert declarado["bytes"] == 112500000 and declarado["estimado"] is False
    # el origen no declara el tamaño del audio: se estima con su bitrate y la duración, y se dice que es estimado
    estimado = next(f for f in p["audio"] if f["id"] == "249")
    assert estimado["bytes"] == int(64 * 1000 / 8 * 600) and estimado["estimado"] is True
    assert next(f for f in p["audio"] if f["id"] == "140")["idioma"] == "es"


# ------------------------------------------------------------------ 3 · la extensión compatible

def test_la_extension_compatible_renombra_el_opus_sin_tocar_el_contenido(tmp_path):
    origen = tmp_path / "canción.opus"
    origen.write_bytes(b"OggS" + bytes(range(256)) * 40)
    antes = origen.read_bytes()

    salidas = extension_compatible([str(origen)])

    assert salidas == [str(tmp_path / "canción.ogg")]
    destino = Path(salidas[0])
    assert destino.is_file() and not origen.exists()                 # se renombró, no se copió
    assert destino.read_bytes() == antes and destino.stat().st_size == len(antes)
    # lo que no es Opus no se toca (un .m4a es AAC: la extensión ya es la que le toca)
    otro = tmp_path / "otra.m4a"
    otro.write_bytes(b"ftyp")
    assert extension_compatible([str(otro)]) == [str(otro)] and otro.is_file()


def test_un_ogg_que_ya_existe_no_se_pisa(tmp_path):
    """Perder el fichero de alguien para cambiarle la extensión no es un arreglo: se deja el .opus como está."""
    origen = tmp_path / "a.opus"
    origen.write_bytes(b"nuevo")
    (tmp_path / "a.ogg").write_bytes(b"viejo")
    assert extension_compatible([str(origen)]) == [str(origen)]
    assert origen.read_bytes() == b"nuevo" and (tmp_path / "a.ogg").read_bytes() == b"viejo"


@pytest.mark.parametrize("compatible", [False, True])
def test_el_interruptor_decide_la_extension_del_fichero_descargado(plan_env, media_dir, compatible):  # noqa: F811
    """De punta a punta con el yt-dlp falso: con el interruptor sale un .ogg, sin él un .opus, y es el mismo
    fichero (mismo tamaño y mismo contenido): la descarga no recodifica nada."""
    tmp_path, _arglog = plan_env

    async def fn(server, c):
        item = await c.call("ytdl.download", {"url": CLIP, "preset": "audio_opus_origen",
                                              "options": {"ext_compatible": compatible}})
        d = item
        for _ in range(600):
            d = await c.call("ytdl.downloads.get", {"id": item["id"]})
            if d["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.1)
        assert d["status"] == "done", d
        salida = Path(d["outputs"][0])
        assert salida.suffix == (".ogg" if compatible else ".opus"), d["outputs"]
        assert salida.is_file()
        original = media_dir / "video30.mkv"                 # lo que «baja» el falso, copiado tal cual
        assert salida.stat().st_size == original.stat().st_size
        assert salida.read_bytes() == original.read_bytes()

    with_server(tmp_path, fn)
