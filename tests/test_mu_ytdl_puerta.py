"""H42 · una sola puerta para abrir y descargar (headless mpv + mpvd + el yt-dlp falso).

Se pega lo que sea en un único campo —un enlace, dos, una lista de reproducción o una ruta local— y en los cuatro
casos sale la misma pregunta, reproducir o descargar, diciendo cuántos elementos hay. Antes había seis puertas
distintas y había que saber de antemano qué ibas a pegar."""

from __future__ import annotations

import json

from tests.test_mu_ytdl import send_event, wait_view, ytdl_mpv  # noqa: F401 - fixture


def gate_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-gate-event", json.dumps({**base, **ev}))


def titles(v):
    return [i["title"] for i in v.get("items", [])]


def hint(v, title):
    return next(i["hint"] for i in v.get("items", []) if i["title"] == title)


def ytdl_state(h, pred, timeout: float = 30.0):
    return h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and pred(v), timeout=timeout)


def open_gate(h):
    h.command("script-binding", "mu_ytdl/ytdl-gate")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-ytdl-gate", timeout=10)
    return ytdl_state(h, lambda v: v.get("view") == "gate")


def pregunta(h, query: str, fila: str, pred=None):
    """Escribe `query` en la caja, pulsa la fila que reconoce lo pegado y devuelve la pregunta."""
    gate_event(h, {"type": "search", "query": query})
    v = ytdl_state(h, lambda v: fila in titles(v))
    gate_event(h, {"type": "activate", "index": 1,
                   "value": next(i for i in v["items"] if i["title"] == fila)["value"]})
    return ytdl_state(h, lambda v: v.get("view") == "what" and "Reproducir" in titles(v)
                      and (pred is None or pred(v)), timeout=60)


def test_un_campo_cuatro_casos_y_una_sola_pregunta(ytdl_mpv, media_dir):
    """A1/A2 · los cuatro casos del criterio: un enlace, dos enlaces, una lista y una ruta local. En todos aparece
    la pregunta reproducir/descargar con el número correcto de elementos."""
    h, _d, _arglog, _tmp = ytdl_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)

    # A3 · la raíz del menú de descargas tiene UNA puerta, no tres
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    v = ytdl_state(h, lambda v: v.get("view") == "root" and bool(v.get("items")))
    assert titles(v)[0] == "Abrir o descargar…"
    for ida in ("Abrir URL…", "Buscar en YouTube…", "Descargar…", "Suscripciones"):
        assert ida not in titles(v), titles(v)

    open_gate(h)
    # la caja vacía ya dice qué acepta
    v = ytdl_state(h, lambda v: v.get("view") == "gate")
    assert v["gate_query"] == ""

    # (a) un enlace de vídeo → 1 elemento, y sin pedirle nada a la red para contarlo
    v = pregunta(h, "https://fake.test/uno", "Seguir con ese enlace")
    assert titles(v)[0] == "Un enlace de internet"
    assert hint(v, "Reproducir") == "1 elemento" and hint(v, "Descargar") == "1 elemento"
    assert v["gate"] == {"kind": "url", "count": 1, "list": False}

    # (b) dos enlaces pegados (los repetidos se descartan) → 2 elementos
    send_event(h, {"type": "back"})
    ytdl_state(h, lambda v: v.get("view") == "gate")
    v = pregunta(h, "https://fake.test/uno https://fake.test/dos, https://fake.test/uno",
                 "Seguir con 2 enlaces")
    assert titles(v)[0] == "Varios enlaces"
    assert hint(v, "Reproducir") == "2 elementos" and hint(v, "Descargar") == "2 elementos"

    # (c) una lista de reproducción → los 5 que dice mpvd, no «1 enlace»
    send_event(h, {"type": "back"})
    ytdl_state(h, lambda v: v.get("view") == "gate")
    v = pregunta(h, "https://www.youtube.com/playlist?list=PLtest", "Seguir con esa lista o canal",
                 lambda v: (v.get("gate") or {}).get("count") == 5)
    assert titles(v)[0] == "Una lista o un canal"
    assert hint(v, "Reproducir") == "5 elementos" and hint(v, "Descargar") == "5 elementos"
    # y la lista ya resuelta cae en la pantalla de descarga de siempre sin volver a preguntarle a mpvd
    send_event(h, {"type": "activate", "index": 3, "value": {"gate_download": True}})
    v = ytdl_state(h, lambda v: v.get("view") == "picklist" and "Descargar 5" in titles(v), timeout=40)
    assert v["pick"]["source"] == "url" and v["pick"]["total"] == 5

    # (d) una ruta local: el archivo también pasa por la misma pregunta (descargar algo que ya está aquí = convertir)
    open_gate(h)
    local = str(media_dir / "video30.mkv")
    v = pregunta(h, local, "Seguir con ese archivo")
    assert titles(v)[0] == "Un archivo de tu equipo"
    assert hint(v, "Reproducir") == "1 elemento" and hint(v, "Descargar").startswith("1 elemento")
    send_event(h, {"type": "activate", "index": 2, "value": {"gate_play": True}})
    h.wait_property("path", lambda v: v == local, timeout=20)
    assert h.script_errors() == [], h.script_errors()


def test_la_caja_tambien_busca_y_lee_un_txt_de_enlaces(ytdl_mpv, tmp_path):
    """A1 · lo que no es un enlace ni una ruta se busca en YouTube, y un .txt con enlaces se reconoce por lo que
    trae. Un vídeo de 4 GB no se lee buscando «https://»: solo las listas de texto, y con tope de tamaño."""
    h, _d, _arglog, _tmp = ytdl_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    lista = tmp_path / "enlaces.txt"
    lista.write_text("# comentario\nhttps://fake.test/tres\nhttps://fake.test/cuatro\n", encoding="utf-8")

    open_gate(h)
    v = pregunta(h, str(lista), "Seguir con 2 enlaces de " + str(lista))
    assert hint(v, "Descargar") == "2 elementos"
    send_event(h, {"type": "activate", "index": 3, "value": {"gate_download": True}})
    v = ytdl_state(h, lambda v: v.get("view") == "picklist" and "Descargar 2" in titles(v), timeout=40)
    assert v["pick"]["source"] == "links"

    # texto suelto: la caja ofrece buscarlo en YouTube (lo que antes era ctrl+f, una puerta aparte)
    open_gate(h)
    gate_event(h, {"type": "search", "query": "canciones de verano"})
    v = ytdl_state(h, lambda v: any(t.startswith("Buscar «canciones de verano»") for t in titles(v)))
    assert v["view"] == "gate"
    assert h.script_errors() == [], h.script_errors()
