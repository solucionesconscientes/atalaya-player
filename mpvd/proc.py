"""En Windows, ningún proceso hijo abre una ventana de consola.

El reproductor es `mpv` (proceso de ventana) y detrás hay un daemon que lanza binarios de consola: `yt-dlp`,
`ffmpeg`, `ffprobe`, `whisper-cli`, `python -m mpvd`… En Windows, un hijo de consola lanzado desde un proceso de
ventana **recibe su propia ventana** (negra, que parpadea o se queda), salvo que se cree con `CREATE_NO_WINDOW` y
con un `STARTUPINFO` que pida `SW_HIDE`. Las dos cosas a la vez: la bandera cubre el binario de consola y el
`STARTUPINFO` cubre a quien llame después a `ShowWindow` con la información de arranque heredada.

En Linux y macOS no hay nada que ocultar: la función devuelve un diccionario vacío, así que fusionarla en
cualquier llamada no cambia nada.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def spawn_kwargs(*, detach: bool = False) -> dict[str, Any]:
    """Los `kwargs` de creación comunes a todo hijo: `subprocess.run`/`Popen` y `create_subprocess_exec`.

    `detach=True` es para el único caso que se desprende de verdad (relanzar el daemon en su propia sesión):
    devuelve `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` **sin** `CREATE_NO_WINDOW` —son incompatibles, juntos
    `CreateProcess` falla con ERROR_INVALID_PARAMETER— y sin `startupinfo`, que ahí no pinta nada porque el hijo
    no hereda consola alguna.

    Sin estado global: se puede llamar desde cualquier hilo y en cualquier momento.
    """
    if sys.platform != "win32":
        return {}
    if detach:
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    info = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    info.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0), "startupinfo": info}
