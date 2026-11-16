"""mpvd.clipboard: en mpv 0.42 hay que pedir `update-clipboard` antes de leer `clipboard/text` (sin monitorización
devuelve vacío). En 0.41 el comando no existe y el error se ignora: se lee como siempre."""

from __future__ import annotations

import asyncio

from mpvd import clipboard


class Cliente:
    def __init__(self, valor="hola", falla_update=False):
        self.valor, self.falla_update, self.llamadas = valor, falla_update, []

    async def command(self, *args):
        self.llamadas.append(("command",) + args)
        if self.falla_update:
            raise RuntimeError("unrecognized command")

    async def get_property(self, nombre, timeout=None):
        self.llamadas.append(("get_property", nombre))
        return self.valor


def test_lee_despues_de_pedir_la_actualizacion():
    c = Cliente("https://ejemplo.test/v.mp4")
    assert asyncio.run(clipboard.read(c)) == "https://ejemplo.test/v.mp4"
    assert c.llamadas == [("command", "update-clipboard", "text", 100),
                          ("get_property", "clipboard/text")]


def test_sin_update_clipboard_lee_igual():
    """mpv 0.41 no conoce el comando: el fallo no puede impedir leer el portapapeles."""
    c = Cliente("clave", falla_update=True)
    assert asyncio.run(clipboard.read(c)) == "clave"
    assert c.llamadas[-1] == ("get_property", "clipboard/text")


def test_propia_propiedad_para_tests():
    """Los tests apuntan la lectura a un user-data; el refresco se pide igual y no estorba."""
    c = Cliente("desde-user-data")
    assert asyncio.run(clipboard.read(c, "user-data/prueba/portapapeles")) == "desde-user-data"
    assert c.llamadas == [("command", "update-clipboard", "text", 100),
                          ("get_property", "user-data/prueba/portapapeles")]
