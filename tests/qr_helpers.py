"""Decode QR symbols with libzbar through ctypes (no Python bindings needed). ``decode`` returns None when the
library is not installed, so tests can skip."""

from __future__ import annotations

import ctypes
import ctypes.util


def _lib() -> ctypes.CDLL | None:
    name = ctypes.util.find_library("zbar")
    if not name:
        return None
    try:
        return ctypes.CDLL(name)
    except OSError:
        return None


def available() -> bool:
    return _lib() is not None


def decode_gray(width: int, height: int, data: bytes) -> list[str]:
    """Decode every symbol in an 8-bit grayscale image (row-major, ``width*height`` bytes)."""
    zbar = _lib()
    if zbar is None:
        raise RuntimeError("libzbar not available")
    zbar.zbar_image_scanner_create.restype = ctypes.c_void_p
    zbar.zbar_image_create.restype = ctypes.c_void_p
    zbar.zbar_image_first_symbol.restype = ctypes.c_void_p
    zbar.zbar_image_first_symbol.argtypes = [ctypes.c_void_p]
    zbar.zbar_symbol_next.restype = ctypes.c_void_p
    zbar.zbar_symbol_next.argtypes = [ctypes.c_void_p]
    zbar.zbar_symbol_get_data.restype = ctypes.c_char_p
    zbar.zbar_symbol_get_data.argtypes = [ctypes.c_void_p]
    zbar.zbar_symbol_get_data_length.restype = ctypes.c_uint
    zbar.zbar_symbol_get_data_length.argtypes = [ctypes.c_void_p]
    zbar.zbar_image_set_format.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    zbar.zbar_image_set_size.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    zbar.zbar_image_set_data.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    zbar.zbar_scan_image.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    zbar.zbar_image_scanner_set_config.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    zbar.zbar_image_destroy.argtypes = [ctypes.c_void_p]
    zbar.zbar_image_scanner_destroy.argtypes = [ctypes.c_void_p]

    scanner = zbar.zbar_image_scanner_create()
    zbar.zbar_image_scanner_set_config(scanner, 0, 0, 1)  # ZBAR_NONE, ZBAR_CFG_ENABLE, 1
    img = zbar.zbar_image_create()
    fourcc = int.from_bytes(b"Y800", "little")
    zbar.zbar_image_set_format(img, fourcc)
    zbar.zbar_image_set_size(img, width, height)
    buf = ctypes.create_string_buffer(data, len(data))
    zbar.zbar_image_set_data(img, buf, len(data), None)
    n = zbar.zbar_scan_image(scanner, img)
    out: list[str] = []
    if n > 0:
        sym = zbar.zbar_image_first_symbol(img)
        while sym:
            length = zbar.zbar_symbol_get_data_length(sym)
            raw = ctypes.string_at(zbar.zbar_symbol_get_data(sym), length)
            out.append(raw.decode("utf-8", "replace"))
            sym = zbar.zbar_symbol_next(sym)
    zbar.zbar_image_destroy(img)
    zbar.zbar_image_scanner_destroy(scanner)
    return out


def decode_modules(modules: list[list[bool]], scale: int = 6, border: int = 4) -> list[str]:
    n = len(modules) + 2 * border
    w = n * scale
    rows = []
    for r in range(n):
        line = bytearray()
        for c in range(n):
            inside = border <= r < n - border and border <= c < n - border
            dark = inside and modules[r - border][c - border]
            line.extend(b"\x00" * scale if dark else b"\xff" * scale)
        rows.append(bytes(line))
    data = b"".join(row for row in rows for _ in range(scale))
    return decode_gray(w, w, data)
