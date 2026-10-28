"""mpvd tiene que arrancar en Windows, donde no hay fcntl ni /dev/cec.

El fallo que esto vigila: `mpvd/cec.py` importaba `fcntl` a nivel de módulo y `server.py` importa
`mpvd.cec` siempre al arrancar, así que en Windows el demonio moría con ModuleNotFoundError y la
interfaz decía «mpvd no disponible» al abrir la tele.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Simula Windows quitando fcntl del alcance de las importaciones, e importa todos los módulos.
SIMULACION = r"""
import builtins, importlib, pkgutil, sys

real = builtins.__import__


def sin_fcntl(name, *a, **k):
    if name == "fcntl":
        raise ImportError("simulado: Windows no tiene fcntl")
    return real(name, *a, **k)


builtins.__import__ = sin_fcntl
import mpvd

fallos = []
for m in pkgutil.walk_packages(mpvd.__path__, "mpvd."):
    try:
        importlib.import_module(m.name)
    except Exception as exc:  # noqa: BLE001 - el test quiere el detalle
        fallos.append(f"{m.name}: {type(exc).__name__}: {exc}")

if fallos:
    print("\n".join(fallos))
    sys.exit(1)
print("ok")
"""


def test_todos_los_modulos_importan_sin_fcntl() -> None:
    r = subprocess.run([sys.executable, "-c", SIMULACION], cwd=RAIZ, capture_output=True, text=True)
    assert r.returncode == 0, f"módulos que no importan sin fcntl:\n{r.stdout}\n{r.stderr}"
    assert "ok" in r.stdout


def test_cec_queda_inactivo_sin_fcntl() -> None:
    codigo = (
        "import builtins\n"
        "real = builtins.__import__\n"
        "def sin_fcntl(n, *a, **k):\n"
        "    if n == 'fcntl': raise ImportError('sin fcntl')\n"
        "    return real(n, *a, **k)\n"
        "builtins.__import__ = sin_fcntl\n"
        "from mpvd import cec\n"
        "assert cec.fcntl is None\n"
        "print('inactivo')\n"
    )
    r = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "inactivo" in r.stdout
