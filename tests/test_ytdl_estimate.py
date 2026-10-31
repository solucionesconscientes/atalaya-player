"""F3 · la estimación de la ficha: cuánto va a ocupar y cuánto va a tardar, antes de empezar.

Es una estimación y así se dice en la pantalla, pero tiene que ser **coherente**: más bitrate pide más bytes, más
resolución pide más bytes, más CRF pide menos, y un fichero de 10 minutos no puede salir más pequeño que uno de 1
minuto con los mismos ajustes. Y para una copia sin recodificar el tamaño es el que declara el origen, no uno
inventado.
"""

from __future__ import annotations

import pytest

from mpvd.ytdl import policy, presets, transcode
from mpvd.ytdl.presets import DownloadSpec, estimar, spec_conversion

# Un -J reducido pero realista: 1080p H.264, 720p VP9 (dos), Opus de dos bitrates y AAC, con width/height/asr.
INFO = {
    "id": "x", "title": "Clase de física", "duration": 600, "categories": ["Education"],
    "formats": [
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "width": 1920, "height": 1080,
         "fps": 30, "tbr": 4400.5, "filesize": 330000000},
        {"format_id": "247", "ext": "webm", "vcodec": "vp9", "acodec": "none", "width": 1280, "height": 720,
         "fps": 30, "tbr": 1500, "filesize": 112500000},
        {"format_id": "244", "ext": "webm", "vcodec": "vp9", "acodec": "none", "width": 854, "height": 480,
         "fps": 30, "tbr": 800},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 128, "audio_channels": 2,
         "asr": 48000},
        {"format_id": "250", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 64, "audio_channels": 2,
         "asr": 48000},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "audio_channels": 2,
         "asr": 44100},
    ],
}


def _spec(**kw) -> DownloadSpec:
    return spec_conversion("https://example.test/v", **kw)


# ------------------------------------------------------------------ copia sin recodificar

def test_una_copia_estima_lo_que_se_descarga_no_lo_que_se_crea():
    est = estimar(DownloadSpec(url="u", perfil="video_720", kind="video"), INFO)
    assert est["recodificar"] is False
    assert est["bytes"] == est["bytes_origen"] == 117300000      # 112,5 MB de VP9 + el Opus de 64k
    assert est["contenedor"] == "mkv" and est["segundos"] >= 1
    assert est["kbps"] == int(est["bytes"] * 8 / 1000 / 600)


# ------------------------------------------------------------------ solo audio

def test_audio_con_bitrate_fijo_es_bitrate_por_duracion():
    est = estimar(_spec(acod="mp3", audio_bitrate=192), INFO)
    assert est["bytes"] == int(192 * 1000 / 8 * 600)
    assert est["bytes_origen"] == 9600000                        # el Opus de 128k del origen, 600 s
    assert "MP3 192k" in est["descripcion"] and est["contenedor"] == "mp3"
    assert est["segundos"] == transcode.redondear_segundos(
        transcode.segundos_codifica(None, 0, 0, 600) + transcode.segundos_descarga(est["bytes_origen"]))


def test_mas_bitrate_es_mas_tamano_y_el_doble_de_duracion_el_doble_de_bytes():
    corto = {**INFO, "duration": 60}
    a = estimar(_spec(acod="opus", audio_bitrate=64), INFO)
    b = estimar(_spec(acod="opus", audio_bitrate=128), INFO)
    assert b["bytes"] == 2 * a["bytes"]
    c = estimar(_spec(acod="opus", audio_bitrate=64), corto)
    assert a["bytes"] == 10 * c["bytes"]


def test_sin_perdida_sale_de_la_frecuencia_y_los_canales_del_origen():
    wav = estimar(_spec(acod="wav"), INFO)
    flac = estimar(_spec(acod="flac"), INFO)
    # 48 kHz estéreo a 16 bits = 1536 kbps; FLAC se queda en algo más de la mitad
    assert wav["kbps"] == 1536
    assert 800 < flac["kbps"] < wav["kbps"]
    assert wav["bytes"] > flac["bytes"] > 0
    assert "sin pérdida" in flac["descripcion"]


def test_el_audio_de_un_video_que_se_convierte_lleva_su_propio_bitrate():
    est = estimar(_spec(acod="aac", audio_bitrate=96), INFO)
    assert est["descripcion"] == "AAC 96k" and est["contenedor"] == "m4a"


# ------------------------------------------------------------------ vídeo: CRF y bitrate

def test_video_con_crf_estima_y_mas_crf_ocupa_menos():
    alto = estimar(_spec(vcod="h264", acod="aac", altura=720, crf=20), INFO)
    bajo = estimar(_spec(vcod="h264", acod="aac", altura=720, crf=30), INFO)
    assert alto["recodificar"] is True and alto["bytes"] > 0
    assert alto["bytes"] > bajo["bytes"]
    assert "CRF 20" in alto["descripcion"] and "720p" in alto["descripcion"]
    # el resultado no es lo que se descarga: se baja el VP9 de 720p (112,5 MB) y el Opus de 128k (9,6 MB)
    assert alto["bytes_origen"] == 122100000


def test_video_con_bitrate_fijo_es_bitrate_por_duracion():
    est = estimar(_spec(vcod="hevc", acod="aac", audio_bitrate=128, video_bitrate=1000, altura=1080), INFO)
    esperado = int((1000 + 128) * 1000 / 8 * 600)
    assert est["bytes"] == esperado
    assert "1000 kbps" in est["descripcion"] and est["contenedor"] == "mp4"


def test_la_resolucion_pedida_manda_en_el_tamano():
    grande = estimar(_spec(vcod="h264", acod="aac", altura=1080, crf=23), INFO)
    pequeno = estimar(_spec(vcod="h264", acod="aac", altura=480, crf=23), INFO)
    assert grande["bytes"] > pequeno["bytes"] * 2
    assert "1080p" in grande["descripcion"] and "480p" in pequeno["descripcion"]


def test_los_fps_se_respetan_y_cambian_la_estimacion():
    sesenta = estimar(_spec(vcod="h264", acod="aac", altura=720, fps=60, crf=23), INFO)
    treinta = estimar(_spec(vcod="h264", acod="aac", altura=720, fps=30, crf=23), INFO)
    assert sesenta["bytes"] > treinta["bytes"]
    assert "60 fps" in sesenta["descripcion"]


def test_dos_pasadas_y_tarjeta_grafica_se_cuentan_en_la_ficha_y_en_el_tiempo():
    una = estimar(_spec(vcod="h264", acod="aac", audio_bitrate=128, video_bitrate=1500), INFO)
    dos = estimar(_spec(vcod="h264", acod="aac", audio_bitrate=128, video_bitrate=1500, two_pass=True), INFO)
    assert dos["bytes"] == una["bytes"]                      # el mismo bitrate: el mismo tamaño
    assert dos["segundos"] > una["segundos"]                 # pero el doble de trabajo
    assert "2 pasadas" in dos["descripcion"]


# ------------------------------------------------------------------ lo que no se puede

def test_dos_pasadas_sin_bitrate_se_rechazan_al_elegir():
    with pytest.raises(ValueError, match="dos pasadas"):
        _spec(vcod="h264", two_pass=True)


def test_hardware_solo_en_los_dos_codecs_que_codifica_la_tarjeta():
    assert _spec(vcod="hevc", hardware=True).hardware is True
    with pytest.raises(ValueError, match="tarjeta gráfica"):
        _spec(vcod="av1", hardware=True)


def test_codecs_y_canales_fuera_del_catalogo_se_rechazan():
    with pytest.raises(ValueError):
        _spec(vcod="mpeg2")
    with pytest.raises(ValueError):
        _spec(acod="vorbis")
    with pytest.raises(ValueError):
        _spec(acod="mp3", audio_canales="5.1")


def test_un_video_sin_pista_de_video_no_se_puede_convertir_en_video():
    solo_audio = {"duration": 60, "formats": [INFO["formats"][3]]}
    with pytest.raises(ValueError, match="pista de vídeo"):
        estimate = estimar(_spec(vcod="h264"), solo_audio)
        assert estimate          # nunca se llega aquí: la política rechaza antes de estimar


def test_sin_duracion_no_se_estima_un_tamano_falso():
    sin_duracion = {**INFO, "duration": None}
    est = estimar(_spec(acod="mp3", audio_bitrate=192), sin_duracion)
    # sin duración no hay bytes que prometer: se devuelve 0 y el tiempo de la codificación, no un número inventado
    assert est["bytes"] == 0


# ------------------------------------------------------------------ el catálogo

def test_el_catalogo_solo_ofrece_lo_que_este_ffmpeg_sabe_codificar():
    cat = presets.catalogo()
    ids_audio = [a["id"] for a in cat["audio"]]
    ids_video = [v["id"] for v in cat["video"]]
    assert ids_audio and all(i in policy.AUDIO_DESTINO for i in ids_audio)
    assert ids_video and all(i in policy.VIDEO_DESTINO for i in ids_video)
    encs = presets.available_encoders()
    for v in cat["video"]:
        assert transcode.ENC_VIDEO[v["id"]] in encs, v["id"]
    # ALAC sin pérdida y con su extensión: es el que yt-dlp no sabe hacer bien
    alac = next(a for a in cat["audio"] if a["id"] == "alac")
    assert alac["sin_perdida"] is True and alac["contenedor"] == "m4a"
    assert cat["hardware"]["disponible"] is False or cat["hardware"]["codecs"]


def test_la_tarjeta_grafica_se_ofrece_solo_donde_la_ha_visto_hw():
    con = presets.catalogo({"available": True, "device": "/dev/dri/renderD128", "codecs": ["h264"]})
    sin = presets.catalogo({"available": False, "reason": "no hay /dev/dri/renderD*"})
    assert next(v for v in con["video"] if v["id"] == "h264")["hardware"] is True
    assert next(v for v in con["video"] if v["id"] == "hevc")["hardware"] is False
    assert sin["hardware"] == {"disponible": False, "codecs": []}
