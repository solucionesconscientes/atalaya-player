"""docs/ATAJOS.md must document every key bound in mpv-config/input.conf (and stay in sync with the scripts)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def input_conf_bindings() -> list[tuple[str, str]]:
    rows = []
    for line in (ROOT / "mpv-config" / "input.conf").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, rest = line.split(None, 1)
        rows.append((key, rest.split("#")[0].strip()))
    return rows


def test_every_binding_is_documented():
    doc = (ROOT / "docs" / "ATAJOS.md").read_text(encoding="utf-8")
    missing = [key for key, _ in input_conf_bindings() if f"`{key}`" not in doc]
    assert not missing, f"teclas sin documentar en docs/ATAJOS.md: {missing}"
    # every script-binding referenced in the doc exists in a script (mu_*) or is a uosc/mpv one
    lua = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "mpv-config" / "scripts").glob("mu-*/main.lua"))
    lua += (ROOT / "mpv-config" / "scripts" / "mu-core.lua").read_text(encoding="utf-8")
    for key, cmd in input_conf_bindings():
        m = re.match(r"script-binding (mu_\w+)/([\w-]+)", cmd)
        if m:
            assert f"'{m.group(2)}'" in lua, f"{key}: {cmd} no existe en los scripts"


def test_input_conf_solo_tiene_teclas():
    """H46/E1 · `input.conf` se queda SOLO para las teclas.

    Los comentarios `#!` construían un segundo menú (el nativo de uosc, `ctrl+m`): 119 entradas, 40 en el primer
    nivel, peor que el nuestro y donde se colaban las duplicaciones (Biblioteca, Música, Audiolibros y Saltar intro
    aparecían dos veces). Este test impide que vuelva: ni marcas de menú, ni líneas sin tecla que solo servían para
    poner una entrada, ni la tecla que lo abría."""
    text = (ROOT / "mpv-config" / "input.conf").read_text(encoding="utf-8")
    marca = "#" + "!"            # escrito así para que este fichero no lleve la marca
    culpables = [ln for ln in text.splitlines() if marca in ln]
    assert not culpables, f"vuelven las entradas del menú de uosc en input.conf: {culpables[:3]}"
    assert "uosc/menu" not in text, "el menú nativo de uosc vuelve a tener tecla"
    # y no queda ninguna línea comentada que lleve un comando: las que había solo existían para ese menú
    sueltas = [ln for ln in text.splitlines()
               if ln.strip().startswith("#") and ("script-binding" in ln or "cycle-values" in ln)]
    assert not sueltas, f"líneas sin tecla que solo sirven para un menú: {sueltas[:3]}"


def menu_views() -> dict[str, str]:
    """Cada `views.<nombre> = function … end` de mu-menu, en bruto."""
    lua = (ROOT / "mpv-config" / "scripts" / "mu-menu" / "main.lua").read_text(encoding="utf-8")
    trozos = re.split(r"^views\.(\w+) = function", lua, flags=re.M)[1:]
    return dict(zip(trozos[0::2], trozos[1::2], strict=True))


def test_el_menu_no_repite_entradas():
    """H46/E2 · ninguna vista de mu-menu ofrece dos veces lo mismo. En el árbol `#!` pasaba con cuatro cosas
    (Biblioteca, Música, Audiolibros y Saltar intro), una como entrada suelta y otra como submenú."""
    vistas = menu_views()
    assert "root" in vistas and "open" in vistas, sorted(vistas)
    for nombre, cuerpo in vistas.items():
        cuerpo = cuerpo.split("\nviews.")[0]
        titulos = re.findall(r"(?:child|bind|cmd|sub|toggle)\('([^']+)'", cuerpo)
        repes = sorted({t for t in titulos if titulos.count(t) > 1})
        assert not repes, f"views.{nombre} repite: {repes}"
    # y la raíz sigue siendo de ocho categorías o menos (E3)
    cats = re.findall(r"\{ title = '([^']+)', icon", vistas["root"].split("\nviews.")[0]) or []
    lua = (ROOT / "mpv-config" / "scripts" / "mu-menu" / "main.lua").read_text(encoding="utf-8")
    bloque = lua.split("local CATEGORIES = {", 1)[1].split("\n}", 1)[0]
    assert len(re.findall(r"\{ title = '", bloque)) <= 8, bloque


def test_cada_tecla_tiene_nombre_en_la_paleta():
    """H46/E1 · al retirar los comentarios de menú de `input.conf`, los títulos de las teclas pasaron a la tabla
    CURATED de mu-menu, que es de donde los lee la paleta (`alt+p`). Este test comprueba que no se queda ninguna
    tecla sin nombre: antes bastaba con olvidarse del comentario y el comando desaparecía del buscador."""
    lua = (ROOT / "mpv-config" / "scripts" / "mu-menu" / "main.lua").read_text(encoding="utf-8")
    curated = lua.split("local CURATED = {", 1)[1].split("\n}", 1)[0]
    nombrados = set(re.findall(r"cmd = '([^']+)'", curated))
    # las vistas del menú principal también son una forma legítima de llegar
    en_menus = "".join(p.read_text(encoding="utf-8")
                       for p in (ROOT / "mpv-config" / "scripts").glob("mu-*/main.lua"))
    sin_nombre = []
    for key, cmd in input_conf_bindings():
        if cmd in nombrados:
            continue
        m = re.match(r"script-binding ([\w/]+)", cmd)
        if m and (f"'{m.group(1)}'" in en_menus or f'"{m.group(1)}"' in en_menus):
            continue          # se abre desde algún menú: tiene nombre allí
        sin_nombre.append((key, cmd))
    assert not sin_nombre, f"teclas sin nombre en la paleta ni en ningún menú: {sin_nombre}"
