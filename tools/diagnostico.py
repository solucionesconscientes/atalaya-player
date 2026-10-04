#!/usr/bin/env python3
"""Recoge datos mientras se ve algo, para saber POR QUÉ va a tirones (H69/H70).

Se ejecuta con la película ya puesta (o antes: espera a que aparezca el reproductor) y cada dos segundos apunta:

* los fotogramas que mpv ha perdido y los que ha entregado tarde —el tirón, medido, no «me ha parecido»—;
* qué está decodificando y con qué (códec, resolución, hwdec, salida de vídeo);
* la CPU de CADA proceso que tenga algo que ver: el reproductor, el servicio de fondo, el **segundo mpv** de las
  miniaturas, ffmpeg, whisper, fpcalc, llama…;
* la carga del equipo.

Al acabar (Ctrl+C o `--minutos`) imprime un resumen y deja el detalle en un fichero. Eso es lo que hay que pasarme.

    tools/diagnostico.sh              # hasta que pulses Ctrl+C
    tools/diagnostico.sh --minutos 5
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mpvd.mpvipc import MpvIpcClient  # noqa: E402

PROPS = ("path", "media-title", "pause", "video-codec", "video-params/w", "video-params/h", "video-params/pixelformat",
         "hwdec-current", "current-vo", "frame-drop-count", "vo-delayed-frame-count", "estimated-vf-fps",
         "container-fps", "time-pos", "demuxer-cache-duration", "video-bitrate")
INTERESANTES = ("mpv", "python3", "ffmpeg", "ffprobe", "whisper-cli", "fpcalc", "llama-cli", "llama-server", "deno",
                "yt-dlp", "cloudflared")


def runtime_dir() -> Path:
    if os.environ.get("MPV_UOS_RUNTIME_DIR"):
        return Path(os.environ["MPV_UOS_RUNTIME_DIR"])
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "mpv-uos"


def socket_del_reproductor() -> Path | None:
    """El mpv más reciente que esté escuchando (bin/mpv-uos crea un socket por instancia)."""
    socks = sorted(runtime_dir().glob("mpv-*.sock"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return socks[-1] if socks else None


def procesos() -> dict[int, tuple[str, float, float]]:
    """pid → (nombre, segundos de CPU, nice). Solo lee /proc: no toca ningún proceso."""
    out: dict[int, tuple[str, float, float]] = {}
    tck = os.sysconf("SC_CLK_TCK")
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            campos = (d / "stat").read_text().rsplit(") ", 1)[1].split()
            nombre = (d / "comm").read_text().strip()
            if nombre not in INTERESANTES:
                continue
            cpu = (int(campos[11]) + int(campos[12])) / tck
            nice = float(campos[16])
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")[:120]
            out[int(d.name)] = (f"{nombre} [{cmd.strip()}]", cpu, nice)
        except (OSError, IndexError, ValueError):
            continue
    return out


async def recoger(minutos: float, intervalo: float = 2.0) -> dict:
    sock = socket_del_reproductor()
    espera = time.monotonic() + 60
    while sock is None and time.monotonic() < espera:
        print("esperando a que abras algo en Atalaya…", end="\r", flush=True)
        await asyncio.sleep(1)
        sock = socket_del_reproductor()
    if sock is None:
        raise SystemExit("no encuentro ningún reproductor abierto: abre Atalaya y pon la película")

    muestras: list[dict] = []
    cpu0 = {pid: v[1] for pid, v in procesos().items()}
    nombres = {pid: v[0] for pid, v in procesos().items()}
    nice = {pid: v[2] for pid, v in procesos().items()}
    fin = time.monotonic() + minutos * 60 if minutos else float("inf")
    print(f"recogiendo datos de {sock.name} · Ctrl+C para terminar")
    async with MpvIpcClient(str(sock)) as c:
        while time.monotonic() < fin:
            fila: dict = {"t": round(time.monotonic(), 1)}
            for p in PROPS:
                with contextlib.suppress(Exception):
                    fila[p] = await c.get_property(p)
            with contextlib.suppress(OSError):
                fila["carga"] = os.getloadavg()[0]
            ahora = procesos()
            for pid, (nombre, cpu, ni) in ahora.items():
                nombres.setdefault(pid, nombre)
                nice.setdefault(pid, ni)
                cpu0.setdefault(pid, cpu)
            fila["cpu"] = {pid: round(cpu - cpu0.get(pid, cpu), 2) for pid, (_n, cpu, _ni) in ahora.items()}
            muestras.append(fila)
            print(f"  {fila.get('time-pos', 0) or 0:7.0f}s de la película · perdidos: "
                  f"{fila.get('frame-drop-count', 0)} · carga {fila.get('carga', 0):.1f}   ", end="\r", flush=True)
            await asyncio.sleep(intervalo)
    return {"socket": str(sock), "muestras": muestras, "nombres": nombres, "nice": nice}


def resumen(datos: dict) -> str:
    m = datos["muestras"]
    if not m:
        return "sin muestras"
    primera, ultima = m[0], m[-1]

    def primero(clave):
        for x in m:
            if x.get(clave) not in (None, 0, ""):
                return x[clave]
        return None
    segundos = max(1.0, ultima["t"] - primera["t"])
    perdidos = (ultima.get("frame-drop-count") or 0) - (primera.get("frame-drop-count") or 0)
    tarde = (ultima.get("vo-delayed-frame-count") or 0) - (primera.get("vo-delayed-frame-count") or 0)
    lineas = [
        "===== RESUMEN =====",
        f"archivo        : {primero('media-title') or primero('path')}",
        f"vídeo          : {primero('video-codec')} · {primero('video-params/w')}x"
        f"{primero('video-params/h')} · {primero('video-params/pixelformat')} · "
        f"{(primero('container-fps') or 0):.3f} fps",
        f"decodificación : hwdec={primero('hwdec-current')} · salida={primero('current-vo')}",
        f"bitrate        : {(primero('video-bitrate') or 0) / 1e6:.1f} Mbps",
        f"medido durante : {segundos / 60:.1f} min",
        f"FOTOGRAMAS PERDIDOS: {perdidos}  ({perdidos / (segundos / 60):.1f} por minuto)",
        f"entregados tarde   : {tarde}",
        f"carga media        : {sum(x.get('carga', 0) for x in m) / len(m):.2f}  (4 núcleos)",
        "",
        "CPU por proceso (núcleos usados de media mientras mirabas):",
    ]
    total: defaultdict[int, float] = defaultdict(float)
    for pid, cpu in ultima.get("cpu", {}).items():
        total[pid] = cpu
    for pid, cpu in sorted(total.items(), key=lambda x: -x[1])[:12]:
        if cpu < 0.5:
            continue
        lineas.append(f"  {cpu / segundos * 100:6.1f} %  nice {datos['nice'].get(pid, 0):>3.0f}  "
                      f"{datos['nombres'].get(pid, pid)}")
    mpvs = [p for p, n in datos["nombres"].items() if n.startswith("mpv") and total.get(p, 0) > 0.5]
    if len(mpvs) > 1:
        lineas += ["", "⚠ había MÁS DE UN mpv gastando CPU: casi seguro las miniaturas (thumbfast)."]
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=0.0, help="0 = hasta Ctrl+C")
    a = ap.parse_args()
    datos: dict = {"muestras": [], "nombres": {}, "nice": {}}
    try:
        datos = asyncio.run(recoger(a.minutos))
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        print(e)
        return 1
    carpeta = ROOT / "tmp" / "diag"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"informe-{time.strftime('%Y-%m-%d_%H.%M')}.json"
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + resumen(datos))
    print(f"\ndetalle: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
