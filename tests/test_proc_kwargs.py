"""Windows: ningún hijo puede abrir una ventana de consola.

Dos cosas: que el ayudante (`mpvd/proc.py`) devuelva lo que debe en cada sistema, y un guardián que recorre
`mpvd/**/*.py` con `ast` y falla si alguien añade una llamada a proceso sin `**spawn_kwargs(...)`. El guardián no
lanza procesos ni importa los módulos: solo lee y parsea.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from mpvd.proc import spawn_kwargs

RAIZ = Path(__file__).resolve().parents[1] / "mpvd"

# Las llamadas que crean un proceso y, por tanto, una consola en Windows.
LANZADORES = {
    ("subprocess", "run"), ("subprocess", "Popen"), ("subprocess", "check_output"),
    ("subprocess", "check_call"), ("subprocess", "call"),
    ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell"),
}

# Excepciones legítimas: «fichero::función» -> motivo. Se revisan a mano, una a una.
EXCEPCIONES = {
    "mpvd/__main__.py::spawn_daemon": "relanzamiento desprendido del daemon: arma su propio creationflags "
                                      "(DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP) dentro del dict de kwargs, y "
                                      "DETACHED_PROCESS es incompatible con CREATE_NO_WINDOW",
}


def _es_lanzador(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
            and (f.value.id, f.attr) in LANZADORES)


def _protegida(call: ast.Call) -> bool:
    for k in call.keywords:
        if k.arg == "creationflags":
            return True
        if (k.arg is None and isinstance(k.value, ast.Call)
                and isinstance(k.value.func, ast.Name) and k.value.func.id == "spawn_kwargs"):
            return True
    return False


def _sueltas(nodo: ast.AST, donde: str, fuera: list[tuple[str, int]], funcion: str = "") -> None:
    """Acumula en `fuera` los lanzamientos sin protección, con la función que los contiene."""
    for hijo in ast.iter_child_nodes(nodo):
        dentro = hijo.name if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)) else funcion
        if isinstance(hijo, ast.Call) and _es_lanzador(hijo) and not _protegida(hijo):
            fuera.append((f"{donde}::{funcion}", hijo.lineno))
        _sueltas(hijo, donde, fuera, dentro)


def _todas() -> list[tuple[str, int]]:
    fuera: list[tuple[str, int]] = []
    for ruta in sorted(RAIZ.glob("**/*.py")):
        arbol = ast.parse(ruta.read_text(), filename=str(ruta))
        _sueltas(arbol, str(ruta.relative_to(RAIZ.parent)), fuera)
    return fuera


def test_todo_lanzamiento_de_mpvd_oculta_la_consola():
    sin_permiso = [f"{clave}:{linea}" for clave, linea in _todas() if clave not in EXCEPCIONES]
    assert not sin_permiso, ("lanzan un proceso sin **spawn_kwargs() (abrirían consola en Windows): "
                             + ", ".join(sin_permiso))


def test_las_excepciones_siguen_haciendo_falta():
    # Si una excepción se arregla, la lista blanca tiene que encogerse: no se queda ahí de adorno.
    vistas = {clave for clave, _ in _todas()}
    for clave, motivo in EXCEPCIONES.items():
        assert motivo.strip(), clave
        assert clave in vistas, f"la excepción {clave} ya no hace falta: quítala de la lista"


@pytest.mark.skipif(sys.platform == "win32", reason="aquí se comprueba el camino de los demás sistemas")
def test_fuera_de_windows_no_hace_nada():
    assert spawn_kwargs() == {}
    assert spawn_kwargs(detach=True) == {}


def test_en_windows_simulado_oculta_la_ventana(monkeypatch):
    class FakeStartupinfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = 0

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "STARTUPINFO", FakeStartupinfo, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)

    kw = spawn_kwargs()
    assert kw["creationflags"] == 0x08000000
    assert kw["startupinfo"].dwFlags & 1 and kw["startupinfo"].wShowWindow == 0
    # Cada llamada trae su propio STARTUPINFO: nada de estado compartido entre hilos.
    assert spawn_kwargs()["startupinfo"] is not kw["startupinfo"]

    suelto = spawn_kwargs(detach=True)
    assert suelto["creationflags"] == 0x00000008 | 0x00000200
    assert not suelto["creationflags"] & 0x08000000  # CREATE_NO_WINDOW y DETACHED_PROCESS no van juntos
    assert "startupinfo" not in suelto
