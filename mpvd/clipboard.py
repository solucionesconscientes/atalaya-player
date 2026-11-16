"""Leer el portapapeles del reproductor.

mpv 0.42 dejó de devolver el contenido actual en ``clipboard/text`` cuando no hay monitorización
(``--clipboard-monitor`` está en «no» por defecto, y en Wayland solo la tiene el backend ``wayland``): hay que
pedir ``update-clipboard text`` antes de leer o el texto llega vacío, y la app diría «el portapapeles está
vacío» teniendo algo copiado. En 0.41 el comando no existe: el error se ignora y se lee como siempre, así que
esto no cambia nada en el mpv del sistema.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("mpvd.clipboard")

CLIPBOARD_PROP = "clipboard/text"


async def refresh(client: Any, kind: str = "text", timeout: int = 100) -> None:
    """Pide al reproductor que actualice el portapapeles. Nunca falla hacia fuera."""
    try:
        await client.command("update-clipboard", kind, timeout)
    except Exception:  # noqa: BLE001 - en mpv < 0.42 el comando no existe y no pasa nada
        log.debug("update-clipboard no disponible (mpv < 0.42 o sin portapapeles)")


async def read(client: Any, prop: str = CLIPBOARD_PROP, timeout: float = 5) -> Any:
    """El texto del portapapeles tal como lo ve el reproductor conectado."""
    await refresh(client)
    return await client.get_property(prop, timeout=timeout)
