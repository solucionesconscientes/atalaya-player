"""H72 · el lanzador mira la versión de mpv y avisa, pero sobre todo NO se cae al mirarla.

El paquete usa el mpv del sistema (ADR-067), y en Debian 13 o Raspberry Pi OS puede ser más viejo que el probado:
la decisión fue instalarse y avisar al arrancar en vez de negarse a instalar. Al implementarlo apareció un fallo
que vale más que la propia función: leer la versión con `mpv --version | head -1` le cierra la salida a mpv, que
muere con SIGPIPE, y como el lanzador corre con `set -o pipefail` eso **abortaba el lanzador antes de arrancar
mpv**. Lo cazó el test del paquete (el reproductor no abría), y por eso aquí se comprueba, con un mpv de pega que
escribe mucho, que el lanzador llega siempre a ejecutar mpv.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def mpv_de_pega(tmp_path: Path, primera_linea: str, lineas: int = 500) -> tuple[Path, Path]:
    """Un mpv que escribe `primera_linea` y muchas más (para que la tubería tenga de qué cerrarse), y que al ser
    llamado de verdad apunta sus argumentos en un fichero y sale."""
    apuntes = tmp_path / "argumentos.txt"
    falso = tmp_path / "mpv-de-pega"
    falso.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{primera_linea}"\n'
        f'  i=0; while [ "$i" -lt {lineas} ]; do echo "relleno $i"; i=$((i + 1)); done\n'
        "  exit 0\n"
        "fi\n"
        f'printf "%s\\n" "$@" > "{apuntes}"\n'
        "exit 0\n",
        encoding="utf-8")
    falso.chmod(0o755)
    return falso, apuntes


def lanzar(tmp_path: Path, falso: Path) -> subprocess.CompletedProcess[str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("MPV_UOS_")}
    entorno |= {"MPV_UOS_MPV": str(falso), "MPV_UOS_RUNTIME_DIR": str(tmp_path / "rt"),
                "MPV_UOS_DATA_DIR": str(tmp_path / "datos"), "HOME": str(tmp_path / "casa")}
    return subprocess.run([str(ROOT / "bin/mpv-uos"), "ficticio.mkv"], capture_output=True, text=True,
                          env=entorno, timeout=60)


@pytest.mark.parametrize(("version", "espera_aviso"), [
    ("mpv v0.41.0 Copyright © 2000-2025 mpv/MPlayer/mplayer2 projects", False),
    ("mpv v0.42.1 Copyright © 2000-2026 mpv/MPlayer/mplayer2 projects", False),
    ("mpv v1.0.0 Copyright © 2030 mpv", False),
    ("mpv v0.40.0 Copyright © 2000-2025 mpv/MPlayer/mplayer2 projects", True),
    ("mpv v0.35.1 Copyright © 2000-2023 mpv/MPlayer/mplayer2 projects", True),
])
def test_avisa_solo_cuando_el_mpv_es_mas_viejo_que_el_probado(tmp_path, version, espera_aviso):
    falso, apuntes = mpv_de_pega(tmp_path, version)
    r = lanzar(tmp_path, falso)
    assert apuntes.is_file(), f"el lanzador no llegó a ejecutar mpv (código {r.returncode}): {r.stderr[-300:]}"
    args = apuntes.read_text(encoding="utf-8")
    assert ("mu-core-mpv_old" in args) is espera_aviso, args[-400:]
    if espera_aviso:
        esperado = version.split()[1].lstrip("v").rsplit(".", 1)[0]
        assert f"mu-core-mpv_old={esperado}" in args, args[-400:]


@pytest.mark.parametrize("salida", [
    "mpv v0.41.0 Copyright © 2000-2025",          # lo normal
    "no soy mpv",                                  # algo que no dice su versión
    "",                                            # nada
    "mpv vraro",                                   # una versión que no se entiende
])
def test_el_lanzador_nunca_se_cae_leyendo_la_version(tmp_path, salida):
    """El fallo de verdad: con `| head -1` y `pipefail`, mpv moría con SIGPIPE y el lanzador se abortaba ahí
    mismo, sin abrir el reproductor y sin decir nada. Pase lo que pase al preguntar la versión, hay que llegar a
    ejecutar mpv."""
    falso, apuntes = mpv_de_pega(tmp_path, salida, lineas=2000)
    r = lanzar(tmp_path, falso)
    assert apuntes.is_file(), f"no llegó a ejecutar mpv (código {r.returncode}): {r.stderr[-300:]}"
    assert "ficticio.mkv" in apuntes.read_text(encoding="utf-8")


def test_mu_core_sabe_recibir_el_aviso():
    """La opción tiene que existir en el script, o el lanzador hablaría solo."""
    lua = (ROOT / "mpv-config/scripts/mu-core.lua").read_text(encoding="utf-8")
    assert "mpv_old = ''" in lua
    assert "opts.mpv_old ~= ''" in lua, "y tiene que usarla para decir algo"
