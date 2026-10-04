"""Lo pesado NUNCA compite con la reproducción (H69).

La regla del proyecto era esa desde el principio, pero se aplicaba a medias: las conversiones, la retransmisión de
una sala, las cadenas de suscripciones y el etiquetado de música ya bajaban su prioridad, y **los tres procesos más
caros no**: `whisper-cli` (subtítulos IA), `llama-cli` (el resumen) y el `fpcalc`/`ffmpeg` que busca la intro. Esos
tres pelean de igual a igual con el decodificador de vídeo, y en un portátil de cuatro núcleos con una película
que se decodifica por software (HEVC 10 bits, por ejemplo: ningún Skylake lo hace por hardware) eso se ve como
tirones. Con mpv a secas no pasa porque mpv no tiene a nadie detrás haciendo trabajo.

Dos cosas, las dos sin privilegios y las dos en el hijo:
* **CPU**: `nice`. 10 para lo normal, 15 para lo que puede durar media hora (voz, resumen).
* **Disco**: clase `idle` de la prioridad de E/S, que es lo que importa cuando el trabajo de fondo lee un archivo
  de varios GB mientras el reproductor lee otro. No hay envoltorio en la biblioteca estándar: se llama a
  `ioprio_set` por `ctypes`, y si el número de llamada no se conoce en esta arquitectura, no se hace nada.
"""

from __future__ import annotations

import contextlib
import os
import platform
from typing import Any

NICE = 10
NICE_HEAVY = 15

# ioprio_set por arquitectura (Linux). Lo que no esté aquí se queda sin tocar la prioridad de disco.
_IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "armv7l": 314, "i686": 289}
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_IDLE = 3


def _io_idle() -> None:  # pragma: no cover - corre en el hijo
    numero = _IOPRIO_SET.get(platform.machine())
    if numero is None:
        return
    with contextlib.suppress(Exception):
        import ctypes  # noqa: PLC0415 - solo aquí

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall(numero, _IOPRIO_WHO_PROCESS, 0, _IOPRIO_CLASS_IDLE << 13)


def lower(nice: int = NICE) -> None:  # pragma: no cover - corre en el hijo
    """Baja la prioridad de CPU y de disco de ESTE proceso. Se pasa como `preexec_fn`."""
    with contextlib.suppress(OSError):
        os.nice(nice)
    _io_idle()


def heavy() -> None:  # pragma: no cover - corre en el hijo
    lower(NICE_HEAVY)


def background(kwargs: dict[str, Any] | None = None, nice: int = NICE) -> dict[str, Any]:
    """Los `kwargs` de un `subprocess`/`create_subprocess_exec` para que arranque en segundo plano de verdad."""
    out = dict(kwargs or {})
    if hasattr(os, "nice"):
        out["preexec_fn"] = heavy if nice >= NICE_HEAVY else lower
    return out
