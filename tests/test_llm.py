"""H38/G3-G6 · el modelo local que escribe el resumen en prosa.

Aquí no se ejecuta ningún modelo (eso tarda ~40 s y ya se midió en docs/BENCHMARKS_LLM.md): se fija el **contrato** con
`llama-cli`, que es lo que se rompería sin avisar al cambiar de versión, y se comprueba que los minutos del resumen
pasan por el validador antes de llegar a nadie.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mpvd import llm as L
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer

# La forma real de la salida de `llama-cli -st` (b11319), reproducida a mano: cartel, datos del modelo, la lista de
# órdenes, el eco del prompt (RECORTADO, con «(truncated)») y la línea de estadísticas con coma decimal.
RAW = """
Loading model... |-\\|/

build      : b11319-3ec4df42d
model      : /models/gemma.gguf
ftype      : Q4_K - Medium
using custom system prompt

available commands:
  /exit or Ctrl+C     stop or exit
  /clear              clear the chat history


> ÍNDICE DEL VÍDEO (con los minutos reales):
1. [0:00] Primera parte
   - [2:15] Una frase del vídeo
   - [7:00] Otra frase del vídeo  ... (truncated)
[2:15] Aquí empieza la charla. [7:00] Y aquí termina.

[ Prompt: 26,6 t/s | Generation: 5,8 t/s ]


Exiting...
"""


def test_la_salida_de_llama_cli_se_limpia_hasta_dejar_solo_lo_que_escribio_el_modelo():
    prompt = ("ÍNDICE DEL VÍDEO (con los minutos reales):\n1. [0:00] Primera parte\n   - [2:15] Una frase del vídeo\n"
              "   - [7:00] Otra frase del vídeo\n\nEscribe un resumen del vídeo en español, en prosa, de entre 3 y 4 "
              "frases. No añadas títulos ni listas.")
    text, tps = L.clean_output(RAW, prompt)
    assert text == "[2:15] Aquí empieza la charla. [7:00] Y aquí termina."
    assert tps == 5.8                      # la coma decimal del locale no se come el número
    assert "Loading model" not in text and "ÍNDICE" not in text and "truncated" not in text
    # sin estadísticas ni eco reconocible, no se inventa nada: se devuelve lo que haya
    plano, sin_tps = L.clean_output("Solo texto.", "")
    assert plano == "Solo texto." and sin_tps is None


def test_el_prompt_lleva_los_minutos_del_indice_y_pide_una_marca_por_frase():
    secciones = [{"start": 0.0, "title": "Primera", "points": [{"start": 135.0, "text": "Algo"}]},
                 {"start": 300.0, "title": "Segunda", "points": [{"start": 375.0, "text": "Otra cosa"}]}]
    for lang in ("es", "en", "fr"):
        system, prompt, tokens = L.prompt_from_outline(secciones, "short", lang)
        assert "[2:15]" in prompt and "[6:15]" in prompt and "[0:00]" in prompt
        assert tokens == L.LENGTHS["short"]["tokens"]
        # lo que arregló el banco de pruebas: se exige una marca al principio de CADA frase y nada de rangos
        assert "EMPIEZA" in system or "STARTS" in system or "COMMENCE" in system
        assert "1:00-2:00" in prompt
    _s, prompt_largo, tokens_largo = L.prompt_from_outline(secciones, "long", "es")
    assert tokens_largo > tokens and "8 y 12" in prompt_largo
    # un idioma desconocido cae a español en vez de dejar el prompt a medias
    system, prompt, _t = L.prompt_from_outline(secciones, "short", "xx")
    assert "español" in prompt


def test_el_catalogo_fija_las_sumas_y_el_modelo_de_serie():
    assert L.DEFAULT_MODEL in L.CATALOG
    for m in L.CATALOG.values():
        assert len(m.sha256) == 64 and m.size_bytes > 100_000_000 and m.note
        assert m.url.startswith("https://huggingface.co/")
    # el de serie es el rápido: lo decidió el banco de pruebas (docs/BENCHMARKS_LLM.md)
    assert L.CATALOG[L.DEFAULT_MODEL].size_bytes < 1_000_000_000


def test_lo_que_el_modelo_invente_no_llega_al_menu(tmp_path, monkeypatch):
    """El trabajo de prosa valida las marcas contra el subtítulo y parte el texto en filas con su minuto."""
    srt = tmp_path / "x.srt"
    rows = [{"start": i * 20.0, "end": i * 20.0 + 19, "text": f"Frase {i} del vídeo sobre el tema principal."}
            for i in range(20)]

    def ts(t: float) -> str:
        ms = int(t * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    srt.write_text("\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n"
                             for i, c in enumerate(rows, 1)), encoding="utf-8")
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)
    server = MpvdServer(settings)

    # un modelo de mentira: una marca buena, una movible, una inventada y un rango
    async def fake_generate(model_name, system, prompt, tokens, timeout=0.0):
        return {"text": "[0:20] Esto sí pasa. [0:33] Esto está cerca. [59:00] Esto no existe. [0:40-1:00] Un rango.",
                "model": model_name, "tokens_per_second": 7.0}

    monkeypatch.setattr(server.recap.llm, "generate", fake_generate)
    monkeypatch.setattr(server.recap.llm, "binary", tmp_path / "llama-cli", raising=False)   # `available` lo mira
    monkeypatch.setattr(server.recap.llm.store, "find", lambda name: tmp_path / "fake.gguf")

    async def go():
        await server.start()
        try:
            r = await server.recap.prose({"sub_path": str(srt), "duration": 400.0, "length": "short",
                                          "language": "es"})
            assert r["status"] == "queued"
            job = server.jobs.get(r["job"]["id"])
            for _ in range(200):
                await asyncio.sleep(0.05)
                if job.status in ("done", "failed", "cancelled"):
                    break
            assert job.status == "done", job.error
            data = job.result
            assert data["marks"] == {"kept": 2, "moved": 1, "removed": 1}
            assert "59:00" not in data["text"], data["text"]
            assert "[0:40-1:00]" not in data["text"] and "[0:40]" in data["text"]   # el rango se queda en su inicio
            # filas con su segundo, listas para el menú
            segundos = [row["start"] for row in data["rows"]]
            assert segundos == [20, 40, 40], data["rows"]
            assert all(row["text"] for row in data["rows"])
            # la segunda vez sale de la caché, sin volver a pensar
            again = await server.recap.prose({"sub_path": str(srt), "duration": 400.0, "length": "short",
                                              "language": "es"})
            assert again["status"] == "done" and again["cached"] is True
        finally:
            await server.stop()

    asyncio.run(go())


def test_sin_modelo_se_dice_que_hace_falta_y_no_se_intenta(tmp_path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)
    server = MpvdServer(settings)
    monkeypatch.setattr(server.recap.llm, "binary", None, raising=False)
    with pytest.raises(RpcError) as e:
        asyncio.run(server.recap.prose({"cues": [{"start": 0, "end": 1, "text": "hola"}], "length": "short"}))
    assert "llama-cli" in e.value.message and e.value.data["install"].endswith("vendor_llama.sh")
    # y con binario pero sin modelo, se dice qué hay que bajar y cuánto pesa
    monkeypatch.setattr(server.recap.llm, "binary", tmp_path / "llama-cli", raising=False)
    monkeypatch.setattr(server.recap.llm.store, "find", lambda name: None)
    with pytest.raises(RpcError) as e:
        asyncio.run(server.recap.prose({"cues": [{"start": 0, "end": 1, "text": "hola"}], "length": "short"}))
    assert "no está descargado" in e.value.message and e.value.data["size_mb"] > 100
    # parámetros imposibles: se rechazan antes de tocar nada
    for bad in ({"length": "mediano"}, {"language": "de"}, {"model": "inventado"}):
        with pytest.raises(RpcError):
            asyncio.run(server.recap.prose({"cues": [{"start": 0, "end": 1, "text": "hola"}], **bad}))
