#!/usr/bin/env python3
"""Comprueba las listas de TV/radio y unos canales de muestra con el mpv de ESTE equipo.

Sirve igual en Linux y en Windows:

    tools/verificar-iptv.py                 # listas + 3 canales por lista
    tools/verificar-iptv.py --solo-listas
    tools/verificar-iptv.py --limite 5 --tiempo 20

En Windows, desde la carpeta de Atalaya:

    .venv\\Scripts\\python.exe tools\\verificar-iptv.py

No modifica nada: descarga a una caché temporal, cuenta canales y abre cada muestra con
`mpv --no-video --frames=1`. Un canal puede fallar por geobloqueo o por estar caído; lo
importante es separar «la lista no carga» de «este canal no responde desde aquí».
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mpvd.net import HttpCache  # noqa: E402
from mpvd.iptv.sources import BUILTIN_SOURCES, load_source  # noqa: E402
from mpvd.iptv.service import IptvService  # noqa: E402


def mpv_bin(arg: str | None) -> str | None:
    if arg:
        return arg if Path(arg).exists() or shutil.which(arg) else None
    for name in ("mpv", "mpv.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def probe(mpv: str | None, url: str, seconds: int) -> dict:
    if not mpv:
        return {"ok": None, "detalle": "mpv no encontrado en PATH"}
    cmd = [mpv, "--no-config", "--no-video", "--ao=null", "--force-window=no",
           "--frames=1", "--length=" + str(seconds), "--msg-level=all=error", "--", url]
    started = time.monotonic()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 15)
        detail = (res.stderr or "").strip().splitlines()[-3:]
        return {"ok": res.returncode == 0, "codigo": res.returncode,
                "segundos": round(time.monotonic() - started, 1), "detalle": " | ".join(detail)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "codigo": None, "segundos": seconds + 15, "detalle": "tiempo agotado"}
    except OSError as exc:
        return {"ok": False, "codigo": None, "detalle": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mpv", help="ruta a mpv (por defecto, el del PATH)")
    ap.add_argument("--limite", type=int, default=3, help="canales de muestra por lista (0 = ninguno)")
    ap.add_argument("--tiempo", type=int, default=20, help="segundos máximos por muestra")
    ap.add_argument("--solo-listas", action="store_true", help="no abre ningún canal")
    ap.add_argument("--salida", help="fichero JSON de salida (por defecto, solo por pantalla)")
    args = ap.parse_args()
    mpv = mpv_bin(args.mpv)
    cache = Path(tempfile.mkdtemp(prefix="atalaya-iptv-"))
    http = HttpCache(cache)
    report = {"fecha": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mpv": mpv, "listas": {}}
    failed = False
    states = {}
    for src in BUILTIN_SOURCES:
        states[src.id] = load_source(http, src, timeout=90)
    # Lo mismo que hace mpvd: España manda y de iptv-org se cae lo que ya esté en TDTChannels.
    service = IptvService.__new__(IptvService)
    service._states = states
    service._drop_cross_source_duplicates()
    for src in BUILTIN_SOURCES:
        st = states[src.id]
        row = {"canales": len(st.channels), "error": st.error, "muestras": []}
        print(f"{src.name}: {row['canales']} canales" + (f" — {st.error}" if st.error else ""))
        if not st.channels:
            failed = True
        if not args.solo_listas:
            for ch in st.channels[:max(0, args.limite)]:
                r = probe(mpv, ch.url, args.tiempo)
                r["canal"] = ch.name
                row["muestras"].append(r)
                print(f"  {'OK ' if r.get('ok') else 'FALLO'}  {ch.name}: {r.get('detalle') or ''}")
                if r.get("ok") is False:
                    failed = True
        report["listas"][src.id] = row
    if args.salida:
        Path(args.salida).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(cache, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
