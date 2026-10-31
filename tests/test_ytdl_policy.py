"""F1 · la política única de descargas: voz/música, 720p VP9, compatible, pistas y la tabla de contenedores.

La referencia es la política ya probada del bot (``files.py`` de ytdlbot): el defecto no recodifica, a igual altura
gana el bitrate más bajo, la voz lleva el Opus más bajo y la música el más alto. Lo que se comprueba aquí son las
decisiones (``policy.plan``) y la orden exacta que sale de ellas (``presets.build_args``).
"""

from __future__ import annotations

import pytest

from mpvd.ytdl import policy
from mpvd.ytdl.policy import Decision, clasificar, contenedor_valido, pistas, plan
from mpvd.ytdl.presets import DownloadSpec, build_args, spec_from_preset

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

# Un -J de YouTube reducido a lo que importa. Los fps y los bitrates son reales de un vídeo de 10 minutos: hay dos
# VP9 a 720p (uno a 2,5 Mbps y otro a 1,5) para poder comprobar que gana el pequeño, y una variante HLS con DRM que
# nunca debe elegirse.
VOZ = {
    "id": "aqz-KE-bpKQ", "title": "Clase de física", "duration": 600, "categories": ["Education"],
    "formats": [
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "fps": 30,
         "tbr": 4400.5, "filesize": 330000000},
        {"format_id": "299", "ext": "mp4", "vcodec": "avc1.64002a", "acodec": "none", "height": 1080, "fps": 60,
         "tbr": 6100},
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "fps": 30,
         "tbr": 2200},
        {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 720, "fps": 30, "tbr": 2500},
        {"format_id": "247", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 720, "fps": 30, "tbr": 1500,
         "filesize": 112500000},
        {"format_id": "244", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 480, "fps": 30, "tbr": 800},
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 360, "tbr": 500},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 128, "audio_channels": 2},
        {"format_id": "250", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 64, "audio_channels": 2},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129},
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none", "protocol": "mhtml"},
        {"format_id": "hls-720", "ext": "mp4", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "tbr": 900,
         "protocol": "m3u8_native", "has_drm": True},
    ],
}
MUSICA = {**VOZ, "categories": ["Music"], "track": "Bolero", "artist": "Autor"}


# ------------------------------------------------------------------ voz o música

def test_clasificar_mira_lo_que_declara_el_origen():
    assert clasificar(VOZ) == "voz"
    assert clasificar(MUSICA) == "musica"
    assert clasificar({"track": "Bolero"}) == "musica"            # sin categoría, basta el track
    assert clasificar({"categorias": ["Music", "Latin"]}) == "musica"   # y la clave en castellano del bot
    assert clasificar({}) == "voz"


# ------------------------------------------------------------------ Opus del origen

def test_opus_origen_voz_coge_el_opus_mas_bajo():
    d = plan(VOZ, "opus_origen")
    assert d == Decision(None, "250", "opus", False, d.nota, d.bytes_estimados)
    assert d.audio_id == "250" and d.video_id is None
    assert d.contenedor == "opus" and d.recodificar is False
    assert "más bajo" in d.nota and "64k" in d.nota


def test_opus_origen_musica_coge_el_opus_mas_alto():
    d = plan(MUSICA, "opus_origen")
    assert d.audio_id == "251" and d.contenedor == "opus"
    assert "música" in d.nota and "128k" in d.nota


def test_opus_origen_respeta_la_pista_elegida_a_mano():
    d = plan(VOZ, "opus_origen", audio_id="251")
    assert d.audio_id == "251" and "copiado tal cual" in d.nota
    with pytest.raises(ValueError, match="no está en el origen"):
        plan(VOZ, "opus_origen", audio_id="999")


# ------------------------------------------------------------------ 720p VP9

def test_video_720_elige_vp9_y_el_de_menor_bitrate_a_igual_altura():
    d = plan(VOZ, "video_720")
    assert (d.video_id, d.audio_id, d.contenedor) == ("247", "250", "mkv")
    assert d.recodificar is False
    assert "VP9 720p" in d.nota and "MKV" in d.nota
    # la copia HLS con DRM declara 900k (menos tbr) y no se elige: no se puede descargar
    assert d.video_id != "hls-720"


def test_video_720_en_musica_lleva_el_opus_bueno():
    assert plan(MUSICA, "video_720").audio_id == "251"


def test_bytes_estimados_suma_lo_declarado_y_estima_lo_que_falta():
    d = plan(VOZ, "video_720")            # 247 declara tamaño; 250 no, y se estima con 64k × 600 s
    assert d.bytes_estimados == 112500000 + int(64 * 1000 / 8 * 600)


# ------------------------------------------------------------------ compatible

def test_compatible_elige_h264_aac_y_mp4():
    d = plan(VOZ, "compatible")
    assert (d.video_id, d.audio_id, d.contenedor) == ("137", "140", "mp4")
    assert d.recodificar is False and "H.264 1080p" in d.nota


def test_compatible_sin_h264_no_miente_con_el_contenedor():
    solo_vp9 = {"duration": 60, "formats": [
        {"format_id": "247", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 720, "tbr": 1500},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 128},
    ]}
    d = plan(solo_vp9, "compatible")
    assert d.contenedor == "mkv" and d.video_id == "247"
    assert "no es el MP4" in d.nota and "el origen no ofrece H.264" in d.nota


# ------------------------------------------------------------------ pistas exactas

def test_pistas_usa_los_ids_y_el_contenedor_de_la_pareja():
    assert plan(VOZ, "pistas", "247", "251").contenedor == "mkv"     # VP9 + Opus
    assert plan(VOZ, "pistas", "137", "140").contenedor == "mp4"     # H.264 + AAC
    d = plan(VOZ, "pistas", "137", "140")
    assert (d.video_id, d.audio_id, d.recodificar) == ("137", "140", False)
    # 137 declara tamaño y 140 no: se estima con sus 129k durante los 600 s del vídeo
    assert d.bytes_estimados == 330000000 + int(129 * 1000 / 8 * 600)
    assert "copiado tal cual" in d.nota


def test_pistas_solo_audio_entrega_el_opus_suelto():
    d = plan(VOZ, "pistas", None, "251")
    assert (d.video_id, d.audio_id, d.contenedor) == (None, "251", "opus")


def test_pistas_rechaza_lo_que_no_es_una_pista():
    with pytest.raises(ValueError, match="audio, no de vídeo"):
        plan(VOZ, "pistas", "251", None)
    with pytest.raises(ValueError, match="al menos una pista"):
        plan(VOZ, "pistas")
    with pytest.raises(ValueError, match="ya trae su propio audio"):
        plan(VOZ, "pistas", "18", "251")       # 18 es progresiva: el audio va dentro


def test_una_pista_progresiva_se_entrega_tal_cual():
    """archive.org no siempre da pistas separadas: el fichero se copia entero, con su audio dentro."""
    d = plan({"duration": 60, "formats": [
        {"format_id": "mp4", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 480, "filesize": 5000000},
    ]}, "video_720")
    assert (d.video_id, d.audio_id, d.contenedor) == ("mp4", None, "mp4")
    assert d.bytes_estimados == 5000000 and "audio dentro" in d.nota


def test_pistas_lista_lo_que_de_verdad_ofrece_el_origen():
    p = pistas(VOZ)
    vids = {f["id"]: f for f in p["video"]}
    auds = {f["id"]: f for f in p["audio"]}
    assert "hls-720" not in vids and "sb0" not in vids            # ni DRM ni storyboards
    assert vids["247"]["altura"] == 720 and vids["247"]["codec"] == "VP9" and vids["247"]["bytes"] == 112500000
    assert auds["250"]["kbps"] == 64 and auds["250"]["codec"] == "Opus" and auds["140"]["familia"] == "aac"
    assert vids["247"]["altura"] >= vids["244"]["altura"]         # el mejor primero
    assert [f["kbps"] for f in p["audio"]][:2] == [128, 64]       # Opus arriba, y de más a menos


# ------------------------------------------------------------------ la tabla de contenedores

@pytest.mark.parametrize("vcodec,acodec,contenedor", [
    ("avc1.640028", "mp4a.40.2", "mp4"),      # H.264 + AAC
    ("hvc1", "mp4a.40.2", "mp4"),             # H.265 + AAC
    ("avc1.640028", "opus", "mkv"),           # H.264 + Opus
    ("vp09.00.10.08", "opus", "mkv"),         # VP9 + Opus
    ("av01.0.13M.08", "mp4a.40.2", "mkv"),    # AV1 + AAC
])
def test_tabla_de_contenedores(vcodec, acodec, contenedor):
    assert contenedor_valido(vcodec, acodec) == (contenedor, None)
    assert contenedor_valido(vcodec, acodec, contenedor)[0] == contenedor


def test_opues_in_mp4_y_aac_en_webm_se_rechazan_con_motivo():
    contenedor, motivo = contenedor_valido("avc1.640028", "opus", "mp4")
    assert contenedor is None and "Opus no es estándar en MP4" in motivo and "MKV" in motivo
    contenedor, motivo = contenedor_valido("vp9", "mp4a.40.2", "webm")
    assert contenedor is None and "WebM no admite AAC" in motivo
    # sin pedido, la misma pareja ya sabe a qué contenedor va
    assert contenedor_valido("vp9", "mp4a.40.2") == ("mkv", None)


# ------------------------------------------------------------------ de la decisión a la orden

def test_build_args_pide_las_dos_pistas_y_no_recodifica():
    spec = DownloadSpec(url=URL, kind="video", video_id="137", audio_id="140",
                        video_codec="avc1.640028", audio_codec="mp4a.40.2", container="mp4")
    argv = build_args(spec, "/tmp/out")
    assert argv[argv.index("-f") + 1] == "137+140"
    assert argv[argv.index("--merge-output-format") + 1] == "mp4"
    assert argv[argv.index("--remux-video") + 1] == "mov>mp4/m4v>mp4/flv>mp4/3gp>mp4"
    assert "-x" not in argv and "--audio-format" not in argv and "--recode-video" not in argv
    assert "-S" not in argv and argv[-2:] == ["--", URL]
    solo = build_args(DownloadSpec(url=URL, kind="video", video_id="247", audio_codec="none",
                                   video_codec="vp9", container="mkv"), "/tmp/out")
    assert solo[solo.index("-f") + 1] == "247" and solo[solo.index("--merge-output-format") + 1] == "mkv"


def test_build_args_rechaza_la_pareja_imposible_antes_de_lanzar():
    spec = DownloadSpec(url=URL, kind="video", video_id="137", audio_id="251",
                        video_codec="avc1.640028", audio_codec="opus", container="mp4")
    with pytest.raises(ValueError, match="Opus no es estándar en MP4"):
        build_args(spec, "/tmp/out")


def test_build_args_de_audio_con_id_extrae_sin_convertir():
    argv = build_args(DownloadSpec(url=URL, kind="video", audio_id="251", audio_codec="opus"), "/tmp/out")
    assert argv[argv.index("-f") + 1] == "251" and "-x" in argv
    assert "--audio-format" not in argv and "--merge-output-format" not in argv


def test_los_presets_nuevos_piden_lo_que_dicen():
    a = build_args(spec_from_preset("audio_opus_origen", URL), "/tmp/out")
    assert a[a.index("-f") + 1] == "ba[acodec^=opus]/ba/b" and "-x" in a and "-S" not in a
    v = build_args(spec_from_preset("video_720", URL), "/tmp/out")
    assert "[vcodec^=vp9][height<=?720]" in v[v.index("-f") + 1]
    assert v[v.index("-S") + 1] == "vcodec:vp9,res,+tbr,acodec:opus"      # a igual altura, el más pequeño
    assert v[v.index("--merge-output-format") + 1] == "mkv"
    c = build_args(spec_from_preset("compatible", URL), "/tmp/out")
    assert "[vcodec^=avc1]" in c[c.index("-f") + 1] and c[c.index("--merge-output-format") + 1] == "mp4"
    p = build_args(spec_from_preset("pistas", URL, {"video_id": "247", "audio_id": "251"}), "/tmp/out")
    assert p[p.index("-f") + 1] == "247+251" and p[p.index("--merge-output-format") + 1] == "mkv"


def test_convertir_solo_lo_dice_y_lo_marca():
    """F1 no convierte: devuelve la decisión marcada para que la fase 3 la ejecute."""
    d = plan(VOZ, "convertir")
    assert d.recodificar is True and "se recodifica" in d.nota
    assert d.video_id == "137"          # convertir parte de la mejor pista: 1080p y, a igual altura, el menor tbr
    assert d.contenedor == "mkv"
    assert plan(VOZ, "video_720").recodificar is False
    with pytest.raises(ValueError, match="perfil de descarga desconocido"):
        plan(VOZ, "inventado")
