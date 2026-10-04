#!/usr/bin/env python3
"""¿Atalaya pesa más que el mpv de siempre? Mídelo con el mismo archivo y contesta con números.

    tools/comparar.py <archivo o URL> [--vueltas 2] [--segundos 25] [--minuto 20]

Abre el mismo medio con `mpv --no-config` y con `bin/mpv-uos`, deja que cada uno arranque y se estabilice, y
mide durante una ventana: fotogramas perdidos y CPU de TODA la pila (el reproductor, el demonio mpvd y lo que
cuelgue de ellos: ffmpeg, whisper, fpcalc, yt-dlp...), no solo del proceso del reproductor.

Dos cosas aprendidas a base de medir mal:
  * el intérprete del .venv se llama `python` en /proc, así que mpvd no salía en ninguna cuenta;
  * si se alterna A, B, A, B, el segundo de cada pareja mide siempre con la CPU más caliente (menos MHz, más
    % para el mismo trabajo). Aquí el orden es A, B, B, A, que anula ese sesgo.

Sin ventanas no se puede medir: el coste que se busca está en pintar. Se abre ventana de verdad (el audio sí se
descarta con --ao=null para no molestar). Los dos reproductores se cierran por PID al terminar cada medición.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from mpvd.mpvipc import MpvIpcClient  # noqa: E402  (después de tocar sys.path)

# Lo que puede estar gastando CPU por culpa del reproductor. `python` es el intérprete del .venv (mpvd).
INTERESA = ("mpv", "python", "python3", "ffmpeg", "ffprobe", "whisper-cli", "fpcalc", "llama-cli", "llama-server",
            "deno", "yt-dlp", "cloudflared")
# El compositor también trabaja por nosotros: cada vez que se repinta el OSD hay que recomponer la ventana, y en
# esta máquina eso cuesta MÁS que descodificar. Medir solo el reproductor dejaba fuera media factura.
ESCRITORIO = ("kwin_wayland", "kwin_x11", "Xwayland", "gnome-shell", "plasmashell", "mutter", "picom", "compton",
              "weston", "sway", "wayfire", "xfwm4", "marco", "compiz")
# Lo que se le pregunta a cada reproductor para poder afirmar que los dos están haciendo el mismo trabajo.
PROPS = ("video-codec", "video-params/pixelfmt", "width", "height", "container-fps", "hwdec-current", "current-vo",
         "vid", "aid", "sid", "deinterlace-active", "video-bitrate")
TCK = os.sysconf("SC_CLK_TCK")


def procesos() -> dict[int, tuple[str, float, str]]:
    """pid → (nombre, segundos de CPU gastados, orden). Solo lee /proc: no toca ningún proceso."""
    out: dict[int, tuple[str, float, str]] = {}
    for d in os.scandir("/proc"):
        if not d.name.isdigit():
            continue
        try:
            nombre = Path(f"/proc/{d.name}/comm").read_text().strip()
            if nombre in ESCRITORIO:
                nombre = f"escritorio({nombre})"
            elif nombre not in INTERESA:
                continue
            cmd = Path(f"/proc/{d.name}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if nombre.startswith("python"):
                if "mpvd" not in cmd:
                    continue        # un python ajeno al proyecto no es parte de la pila
                nombre = "mpvd"
            campos = Path(f"/proc/{d.name}/stat").read_text().rsplit(") ", 1)[1].split()
            out[int(d.name)] = (nombre, (int(campos[11]) + int(campos[12])) / TCK, cmd.strip()[:70])
        except (OSError, IndexError, ValueError):
            continue
    return out


def hilos(pid: int) -> dict[str, float]:
    """tid → segundos de CPU, por nombre de hilo. mpv los nombra (mpv/vo, mpv/demux, un hilo por script Lua...),
    así que esto dice en qué parte del reproductor se va el tiempo sin tener que ir quitando piezas a ciegas."""
    out: dict[str, float] = {}
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return out
    for tid in tids:
        try:
            campos = Path(f"/proc/{pid}/task/{tid}/stat").read_text().rsplit(") ", 1)[1].split()
            nombre = Path(f"/proc/{pid}/task/{tid}/comm").read_text().strip()
        except (OSError, IndexError):
            continue
        out[f"{nombre}:{tid}"] = (int(campos[11]) + int(campos[12])) / TCK
    return out


def mhz() -> int:
    """La frecuencia media de la CPU: si cae entre medición y medición, los porcentajes no son comparables."""
    try:
        v = [float(l.split(":")[1]) for l in Path("/proc/cpuinfo").read_text().splitlines() if l.startswith("cpu MHz")]
        return round(sum(v) / len(v)) if v else 0
    except OSError:
        return 0


async def pasear_raton(c: MpvIpcClient, hasta: float) -> None:
    """Mueve el ratón por la barra de progreso mientras se mide.

    Es el caso que de verdad dolía: al pasar por la barra, uosc se despierta y thumbfast abre un SEGUNDO mpv
    para las miniaturas. Midiendo con el ratón quieto eso no sale nunca, y era justo lo que había que vigilar."""
    ancho = (await c.get_property("osd-dimensions") or {}).get("w") or 1280
    alto = (await c.get_property("osd-dimensions") or {}).get("h") or 720
    x, paso = ancho * 0.3, ancho * 0.02
    while asyncio.get_running_loop().time() < hasta:
        x += paso
        if x > ancho * 0.7 or x < ancho * 0.3:
            paso = -paso
        try:
            await c.command("mouse", int(x), int(alto - 20))     # a 20 px del borde: encima de la barra
        except Exception:
            return
        await asyncio.sleep(0.5)


async def una(cmd: list[str], sock: Path, segundos: float, calentar: float, raton: bool = False) -> dict:
    """Una medición: abre el reproductor, espera a que se estabilice, mide y lo cierra por su PID."""
    sock.unlink(missing_ok=True)
    proc = subprocess.Popen(cmd, cwd=RAIZ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        espera = time.monotonic() + 30
        while not sock.exists() and time.monotonic() < espera:
            if proc.poll() is not None:
                raise SystemExit(f"el reproductor se cerró solo (código {proc.returncode}): {' '.join(cmd[:3])}")
            await asyncio.sleep(0.25)
        if not sock.exists():
            raise SystemExit(f"no apareció el socket {sock}")
        async with MpvIpcClient(str(sock)) as c:
            await asyncio.sleep(calentar)        # arrancar, buscar el minuto y llenar la caché no es medir
            if raton:
                await c.command("mouse", 10, 10)      # que la interfaz esté ya despierta antes de contar
                await asyncio.sleep(1.5)
            propio = await c.get_property("pid")
            hilos0 = hilos(propio) if propio else {}
            antes = {pid: v[1] for pid, v in procesos().items()}
            d0 = await c.get_property("frame-drop-count") or 0
            if raton:
                await pasear_raton(c, asyncio.get_running_loop().time() + segundos)
            else:
                await asyncio.sleep(segundos)
            d1 = await c.get_property("frame-drop-count") or 0
            ahora = procesos()
            como = {}
            for p in PROPS:
                try:
                    como[p] = await c.get_property(p)
                except Exception:                 # una propiedad que este archivo no tiene no es un fallo
                    como[p] = None
            cpu = {}
            for pid, (nombre, usado, cmdline) in ahora.items():
                gasto = usado - antes.get(pid, 0.0)
                if gasto > 0.05:                  # por debajo de eso es ruido de contabilidad del kernel
                    cpu[f"{nombre}:{pid}"] = round(gasto / segundos * 100, 1)
            # El tamaño de la ventana no es un detalle: el OSD se rasteriza al tamaño de la ventana, así que
            # dos mediciones con ventanas distintas no cuestan lo mismo aunque el vídeo sea el mismo.
            # ¿Estaba el puntero encima de la ventana? uosc mantiene la interfaz dibujándose mientras lo está,
            # y entonces cuesta; con el puntero fuera no cuesta nada. Sin este dato las medidas parecen caprichosas.
            raton_encima = ((await c.get_property("mouse-pos")) or {}).get("hover")
            hilos1 = hilos(propio) if propio else {}
            porhilo = {}
            for k, usado in hilos1.items():
                gasto = usado - hilos0.get(k, 0.0)
                if gasto > 0.05:
                    porhilo[k] = round(gasto / segundos * 100, 1)
            d = await c.get_property("osd-dimensions") or {}
            return {"perdidos": d1 - d0, "cpu": cpu, "total_%": round(sum(cpu.values()), 1),
                    "mhz": mhz(), "como": como, "ventana": f"{d.get('w', 0):.0f}x{d.get('h', 0):.0f}",
                    "pantalla_completa": await c.get_property("fullscreen"), "raton_encima": raton_encima,
                    "hilos": dict(sorted(porhilo.items(), key=lambda kv: -kv[1]))}
    finally:
        proc.terminate()                          # solo el PID que hemos lanzado aquí
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
        sock.unlink(missing_ok=True)
        await asyncio.sleep(1.5)                  # que el escritorio recupere la ventana antes de la siguiente


def orden(vueltas: int) -> list[str]:
    """A, B, B, A por vuelta: así ninguno de los dos mide siempre con la CPU recién calentada por el otro."""
    return ["mpv", "atalaya", "atalaya", "mpv"] * vueltas


async def medir(medio: str, vueltas: int, segundos: float, minuto: float, calentar: float, tmp: Path,
                raton: bool = False, completa: bool = False, ventana: str = "1280x720") -> dict:
    comun = [f"--start={minuto * 60:.0f}", "--no-terminal", "--ao=null"]
    if completa:
        comun.append("--fullscreen")        # como se ve una película de verdad
    else:
        # La ventana SIEMPRE del mismo tamaño. Sin esto, el escritorio le da a cada apertura el tamaño que quiere
        # (se vieron 1366x573 y 1920x804 en la misma tanda) y, como el OSD se rasteriza a tamaño de ventana y el
        # vídeo se escala a él, la misma configuración medía 30 % o 40 %. Las medidas parecían caprichosas y lo
        # que cambiaba era la ventana.
        comun += [f"--autofit={ventana}", f"--geometry={ventana}+0+0"]
    acum: dict[str, list[dict]] = {"mpv": [], "atalaya": []}
    for i, cual in enumerate(orden(vueltas)):
        sock = tmp / f"comparar-{cual}-{i}.sock"
        if cual == "mpv":
            cmd = ["mpv", "--no-config", f"--input-ipc-server={sock}", *comun, medio]
        else:
            cmd = [str(RAIZ / "bin/mpv-uos"), f"--input-ipc-server={sock}", *comun, medio]
        r = await una(cmd, sock, segundos, calentar, raton)
        acum[cual].append(r)
        print(f"  {i + 1}/{len(orden(vueltas))} · {cual:8s} perdidos={r['perdidos']:4d}  "
              f"CPU {r['total_%']:5.1f} %  {r['mhz']} MHz  ventana {r['ventana']}  "
              f"{'ratón encima' if r['raton_encima'] else 'ratón fuera'}  {r['cpu']}", flush=True)
    return acum


def resumen(acum: dict[str, list[dict]]) -> str:
    lineas = []
    medias = {}
    for cual, filas in acum.items():
        tot = [f["total_%"] for f in filas]
        medias[cual] = sum(tot) / len(tot)
        lineas.append(f"{cual:8s} n={len(filas)}  CPU {medias[cual]:5.1f} %  (de {min(tot):.1f} a {max(tot):.1f})  "
                      f"fotogramas perdidos: {sum(f['perdidos'] for f in filas)}")
    # El ruido de la máquina: lo que varía el mismo reproductor consigo mismo. Una diferencia por debajo de eso
    # no se puede llamar diferencia.
    ruido = max(max(f["total_%"] for f in filas) - min(f["total_%"] for f in filas) for filas in acum.values())
    dif = medias["atalaya"] - medias["mpv"]
    lineas.append("")
    if abs(dif) <= ruido:
        lineas.append(f"Atalaya y mpv gastan lo mismo: {dif:+.1f} puntos, por debajo del ruido de la máquina "
                      f"(±{ruido:.1f}).")
    else:
        quien = "más" if dif > 0 else "menos"
        lineas.append(f"Atalaya gasta {abs(dif):.1f} puntos {quien} que mpv (ruido de la máquina ±{ruido:.1f}).")
    ventanas = {cual: sorted({f["ventana"] for f in filas}) for cual, filas in acum.items()}
    if len(set(sum(ventanas.values(), []))) > 1:
        lineas.append(f"¡OJO! Las ventanas NO eran del mismo tamaño, así que esto no compara nada "
                      f"(el OSD se dibuja a tamaño de ventana y el vídeo se escala a ella): "
                      f"mpv {', '.join(ventanas['mpv'])} · atalaya {', '.join(ventanas['atalaya'])}")
    def por_hilo(filas):
        tot: dict[str, float] = {}
        for f in filas:
            for k, v in f.get("hilos", {}).items():
                tot[k.split(":")[0]] = tot.get(k.split(":")[0], 0.0) + v
        return {k: round(v / len(filas), 1) for k, v in sorted(tot.items(), key=lambda kv: -kv[1])}

    ha, hm = por_hilo(acum["atalaya"]), por_hilo(acum["mpv"])
    if ha or hm:
        lineas.append("")
        lineas.append("Dentro del reproductor, por hilo (media, % de un núcleo):")
        for k in sorted(set(ha) | set(hm), key=lambda k: -(ha.get(k, 0) + hm.get(k, 0))):
            dif = ha.get(k, 0.0) - hm.get(k, 0.0)
            marca = "  ←" if abs(dif) >= 1.0 else ""
            lineas.append(f"  {k:22s} mpv {hm.get(k, 0.0):5.1f}   atalaya {ha.get(k, 0.0):5.1f}   {dif:+5.1f}{marca}")
    for cual, filas in acum.items():
        encima = [f for f in filas if f["raton_encima"]]
        fuera = [f for f in filas if not f["raton_encima"]]
        if encima and fuera:
            me = sum(f["total_%"] for f in encima) / len(encima)
            mf = sum(f["total_%"] for f in fuera) / len(fuera)
            lineas.append(f"{cual}: con el ratón encima de la ventana {me:.1f} %, con el ratón fuera {mf:.1f} % "
                          f"({me - mf:+.1f})")
    primera = acum["mpv"][0]["como"]
    segunda = acum["atalaya"][0]["como"]
    distinto = {k: (primera.get(k), segunda.get(k)) for k in PROPS if primera.get(k) != segunda.get(k)}
    if distinto:
        lineas.append("Ojo, no están haciendo lo mismo: " + ", ".join(
            f"{k}: mpv={v[0]} atalaya={v[1]}" for k, v in distinto.items()))
    else:
        lineas.append(f"Los dos descodifican igual: {primera.get('video-codec')} {primera.get('width')}x"
                      f"{primera.get('height')} {primera.get('video-params/pixelfmt')}, "
                      f"hwdec={primera.get('hwdec-current')}, vo={primera.get('current-vo')}.")
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compara el gasto de Atalaya con el del mpv de siempre.")
    ap.add_argument("medio", help="archivo o URL que ver con los dos")
    ap.add_argument("--vueltas", type=int, default=2, help="parejas de mediciones (cada vuelta son 4 aperturas)")
    ap.add_argument("--segundos", type=float, default=25, help="ventana de medición")
    ap.add_argument("--minuto", type=float, default=20, help="minuto del medio por el que empezar")
    ap.add_argument("--calentar", type=float, default=8, help="segundos de arranque que NO se miden")
    ap.add_argument("--completa", action="store_true", help="a pantalla completa, como se ve una película")
    ap.add_argument("--ventana", default="1280x720", help="tamaño exacto de la ventana (igual para los dos)")
    ap.add_argument("--raton", action="store_true",
                    help="pasear el ratón por la barra mientras se mide (despierta la interfaz y las miniaturas)")
    a = ap.parse_args()

    tmp = RAIZ / "tmp/diag"
    tmp.mkdir(parents=True, exist_ok=True)
    print(f"comparando con «{Path(a.medio).name}» · minuto {a.minuto:g} · {a.segundos:g} s por medición"
          + (" · a pantalla completa" if a.completa else f" · en ventana de {a.ventana}")
          + (" · con el ratón por la barra" if a.raton else "") + "\n")
    try:
        acum = asyncio.run(medir(a.medio, a.vueltas, a.segundos, a.minuto, a.calentar, tmp, a.raton, a.completa,
                                 a.ventana))
    except KeyboardInterrupt:
        print("\ncortado")
        return 1
    except SystemExit as e:
        print(e)
        return 1
    texto = resumen(acum)
    print("\n" + texto)
    destino = tmp / f"comparacion-{time.strftime('%Y-%m-%d-%H%M')}.json"
    destino.write_text(json.dumps({"medio": a.medio, "mediciones": acum, "resumen": texto}, indent=1,
                                  ensure_ascii=False))
    print(f"\ndetalle en {destino.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
