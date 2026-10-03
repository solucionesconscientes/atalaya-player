"""H58 · «ponme esta charla de una hora en quince minutos».

No es el resumen escrito (eso ya existe): es el VÍDEO montado y acortado. mpvd elige los tramos que mejor
representan lo dicho, cortados por frases enteras, y el reproductor los monta en una línea de tiempo virtual de
mpv (`edl://`), que no recodifica nada y se abre al instante.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

np = pytest.importorskip("numpy")

from tests.test_semantic import _daemon_with_fake, transcript  # noqa: E402


def test_los_tramos_duran_lo_pedido_y_cortan_por_frases(daemon_env, media_dir, tmp_path):
    d = daemon_env
    _daemon_with_fake(d)
    video = tmp_path / "charla.mkv"
    shutil.copy(media_dir / "video30.mkv", video)
    cues = transcript(["cocina", "astronomia", "futbol"], seconds_per_topic=1200)   # una charla de 1 h
    d.call("asr.inject", {"path": str(video), "segments": cues, "duration": 3600.0})
    d.call("semantic.index", {"path": str(video), "wait": True}, timeout=60)

    r = d.call("semantic.highlights", {"path": str(video), "minutes": 15}, timeout=60)
    segs = r["segments"]
    assert segs, r
    # dura lo que se ha pedido (no 17 minutos: unir y estirar infla, y por eso se ajusta cuántas frases se cogen)
    assert 12 * 60 <= r["total"] <= 15 * 60, r["total"]
    # y es un montaje que se puede ver: tramos largos, en orden y sin solaparse
    duraciones = [s["end"] - s["start"] for s in segs]
    assert min(duraciones) >= 20, duraciones
    assert all(segs[i]["end"] <= segs[i + 1]["start"] for i in range(len(segs) - 1))
    # pedir menos da menos
    corto = d.call("semantic.highlights", {"path": str(video), "minutes": 5}, timeout=60)
    assert corto["total"] < r["total"]


def test_el_montaje_virtual_lo_abre_mpv_y_dura_la_suma(daemon_env, media_dir, tmp_path):
    """La pieza que lo hace barato: mpv monta los tramos sin recodificar nada (`edl://`). Y el escapado importa:
    una ruta con una coma rompe el montaje si no se escapa (comprobado: «EDL parsing failed»)."""
    origen = tmp_path / "peli, con coma (2024).mkv"
    shutil.copy(media_dir / "video30.mkv", origen)
    ruta = str(origen)
    trozos = [(2.0, 5.0), (20.0, 24.0)]
    partes = [f"%{len(ruta.encode())}%{ruta},start={a:.3f},length={b - a:.3f}" for a, b in trozos]
    edl = "edl://" + ";".join(partes)

    out = subprocess.run(["mpv", "--no-config", "--vo=null", "--ao=null", "--frames=1",
                          "--term-playing-msg=DUR=${=duration}", edl], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-300:]
    dur = next(float(ln.split("=", 1)[1]) for ln in out.stdout.splitlines() if ln.startswith("DUR="))
    assert abs(dur - 7.0) < 0.5, dur          # 3 + 4 segundos

    # sin escapar, mpv lo rechaza: por eso el reproductor escapa siempre
    crudo = subprocess.run(["mpv", "--no-config", "--vo=null", "--ao=null", "--frames=1",
                            f"edl://{ruta},start=2,length=3"], capture_output=True, text=True, timeout=60)
    assert crudo.returncode != 0 or "EDL" in crudo.stderr + crudo.stdout
