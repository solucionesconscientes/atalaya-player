"""H49 · la maquinaria de los idiomas (ADR-087, diseño en docs/IDIOMAS.md).

Lo que se comprueba aquí es lo que hace que traducir ~1.800 cadenas sea viable: que la cadena castellana sea la
clave, que lo no traducido caiga al castellano (nunca una clave cruda ni un hueco), y que el idioma se decida una
sola vez y con la regla de Ser: castellano o francés → ese; inglés o cualquier otro → inglés.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

import pytest

from mpvd import i18n
from tests.conftest import ROOT, start_mpv
from tests.test_mu_iptv import tv  # noqa: F401
from tests.test_remote import remote_env  # noqa: F401

LANZADOR = ROOT / "bin" / "mpv-uos"


def _con_locale(tmp_path, locale_env: str, **extra: str) -> list[str]:
    """Los argumentos con los que el lanzador llamaría a mpv, con un mpv falso que solo los imprime."""
    fake = tmp_path / "fake-mpv"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    fake.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MPV_UOS_MPV": str(fake),
           "MPV_UOS_RUNTIME_DIR": str(tmp_path), "MPV_UOS_DATA_DIR": str(tmp_path / "data"),
           "LANG": locale_env, **extra}
    out = subprocess.run([str(LANZADOR), "--idle=yes"], capture_output=True, text=True, check=True, env=env,
                         timeout=60)
    return out.stdout.splitlines()


@pytest.mark.parametrize(("locale_env", "esperado"), [
    ("es_ES.UTF-8", "es"), ("es", "es"), ("ca_ES@valencia", "en"),   # catalán no está: inglés, que es la regla
    ("fr_FR.UTF-8", "fr"), ("fr_CA", "fr"),
    ("en_US.UTF-8", "en"), ("en_GB", "en"), ("de_DE.UTF-8", "en"), ("ja_JP.UTF-8", "en"),
    ("", "en"), (None, "en"), ("C", "en"),
])
def test_el_idioma_sale_del_locale_con_la_regla_de_ser(locale_env, esperado):
    assert i18n.from_locale(locale_env) == esperado


@pytest.mark.parametrize(("cabecera", "esperado"), [
    ("fr-CA,fr;q=0.9,en;q=0.8", "fr"),
    ("en-US,en;q=0.9,es;q=0.8", "en"),
    ("es-ES,es;q=0.9,en;q=0.5", "es"),
    ("de,ja;q=0.9", "en"),                       # ninguno de los nuestros
    ("en;q=0.3,fr;q=0.9", "fr"),                 # se respeta la q, no el orden
    ("", "en"), (None, "en"),
])
def test_las_paginas_siguen_al_navegador_del_invitado(cabecera, esperado):
    """Las abre otra persona, que puede estar en otro idioma que el anfitrión: mandan sus preferencias."""
    assert i18n.from_accept_language(cabecera) == esperado


def test_lo_no_traducido_cae_al_castellano_y_el_catalogo_es_coherente():
    """El peor caso tiene que ser «se ve en español», nunca una clave cruda ni un hueco en blanco."""
    i18n.catalogue.cache_clear()
    assert i18n.t("Una cadena que no existe en ningún catálogo", "fr") == "Una cadena que no existe en ningún catálogo"
    assert i18n.t("Lo que sea", "es") == "Lo que sea"      # el castellano es la identidad: no hay es.json
    assert not (ROOT / "locales" / "es.json").exists()
    for lang in ("en", "fr"):
        data = json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        for clave, valor in data.items():
            assert isinstance(clave, str) and isinstance(valor, str) and valor.strip(), (lang, clave)
            # una traducción con marcas de formato tiene que llevar las mismas que el original, o reventará al usarla
            assert clave.count("%s") == valor.count("%s") and clave.count("%d") == valor.count("%d"), (lang, clave)


@pytest.mark.parametrize(("entorno", "esperado"), [("es_ES.UTF-8", "es"), ("fr_FR.UTF-8", "fr"),
                                                   ("de_DE.UTF-8", "en"), ("en_US.UTF-8", "en")])
def test_el_lanzador_decide_el_idioma_antes_de_cargar_ningun_script(entorno, esperado, tmp_path):
    """Lo decide bin/mpv-uos, no cada script: así la pantalla de inicio no sale en un idioma y cambia al siguiente.
    Y a uosc, que ya trae sus traducciones, se le dice cuál usar en vez de duplicarlas."""
    args = _con_locale(tmp_path, entorno)
    assert f"--script-opts-append=mu-core-lang={esperado}" in args, args
    assert f"--script-opts-append=uosc-languages={esperado},slang,en" in args, args


def test_se_puede_forzar_el_idioma_contra_el_del_sistema(tmp_path):
    """Alguien con el sistema en inglés puede querer el reproductor en castellano."""
    args = _con_locale(tmp_path, "en_US.UTF-8", MPV_UOS_LANG="es")
    assert "--script-opts-append=mu-core-lang=es" in args
    # y un valor que no servimos no deja el reproductor a medias: cae al inglés
    args = _con_locale(tmp_path, "en_US.UTF-8", MPV_UOS_LANG="klingon")
    assert "--script-opts-append=mu-core-lang=en" in args


def test_los_scripts_reciben_el_idioma_y_lo_publican(daemon_env):
    """mu-core lo resuelve una vez y lo publica, para que los demás lo lean en vez de volver a calcularlo."""
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-lang=fr,mu-core-autostart=no"],
                  env={**daemon_env.env, "LANG": "es_ES.UTF-8"})
    try:
        st = h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("lang"), timeout=30)
        assert st["lang"] == "fr", "manda lo que dice el lanzador, no el entorno de la máquina"
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()

    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-autostart=no"],
                  env={**daemon_env.env, "LANG": "de_DE.UTF-8"})
    try:
        st = h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("lang"), timeout=30)
        assert st["lang"] == "en", "sin que nadie lo diga, lo saca del entorno: alemán → inglés"
    finally:
        h.stop()


@pytest.mark.parametrize(("lang", "esperados"), [
    ("es", ("Abrir o descargar", "TV y radio", "Preferencias", "Salir")),
    ("en", ("Open or download", "TV and radio", "Preferences", "Quit")),
    ("fr", ("Ouvrir ou télécharger", "TV et radio", "Préférences", "Quitter")),
])
def test_el_menu_sale_en_el_idioma_que_toca(tv, lang, esperados):  # noqa: F811
    """La prueba de verdad: el menú principal, en los tres idiomas, con el reproductor en marcha."""
    _tv_mpv, d, _rec = tv
    h = start_mpv(d.runtime_dir, [f"--script-opts=mu-core-lang={lang},mu-core-watchdog_seconds=2"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=40)
        h.command("script-binding", "mu_menu/root")
        st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "root" and v.get("items"),
                             timeout=20)
        titulos = [i["title"] for i in st["items"]]
        for esperado in esperados:
            assert esperado in titulos, (lang, esperado, titulos)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_una_cadena_sin_traducir_sale_en_castellano_no_en_blanco(tv):  # noqa: F811
    """El peor caso tiene que ser «se ve en español». Se comprueba con el reproductor en inglés y una cadena que
    está a propósito fuera del catálogo (los nombres de las teclas, que no se traducen)."""
    _tv_mpv, d, _rec = tv
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-lang=en,mu-core-watchdog_seconds=2"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=40)
        h.command("script-binding", "mu_menu/root")
        st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "root" and v.get("items"),
                             timeout=20)
        # «TV y radio» lleva el nombre de su tecla como pista: no se traduce y sale tal cual, no vacío
        fila = next(i for i in st["items"] if i["title"] == "TV and radio")
        assert fila["hint"] == "alt+t"
        assert all(i["title"].strip() for i in st["items"]), "ninguna fila puede quedarse sin título"
    finally:
        h.stop()


def test_los_catalogos_estan_completos_y_al_dia():
    """Guarda contra el olvido más fácil: tocar una cadena del código y dejar el catálogo cojo. El extractor es la
    fuente —recorre los scripts— y aquí se comprueba que lo que encuentra está traducido a los dos idiomas."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ex", ROOT / "tools" / "i18n_extract.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)

    # H49/G4 · TODOS los scripts del reproductor usan ya `mu.i18n`: que ninguno se quede atrás también se comprueba
    todos = sorted((ROOT / "mpv-config" / "scripts").glob("mu-*/main.lua"))
    sin_i18n = [p.parent.name for p in todos if "require('mu.i18n')" not in p.read_text(encoding="utf-8")
                and ex.process(p)[1]]
    assert not sin_i18n, f"scripts con texto visible y sin mu.i18n: {sin_i18n}"
    encontradas: set[str] = set()
    for f in todos:
        _, found, glued = ex.process(f)
        encontradas |= set(found)
        assert not glued, f"{f.name}: frases pegadas con `..`, que no se pueden traducir a trozos: {glued}"

    # H49/G5 · y los mensajes de mpvd que acaban en la pantalla, con su propio extractor
    spec_py = importlib.util.spec_from_file_location("ex_py", ROOT / "tools" / "i18n_extract_py.py")
    ex_py = importlib.util.module_from_spec(spec_py)
    spec_py.loader.exec_module(ex_py)
    for f in sorted((ROOT / "mpvd").rglob("*.py")):
        _, found_py, pendientes = ex_py.process(f)
        encontradas |= set(found_py)
        assert not pendientes, f"{f.name}: mensajes en castellano sin envolver: {pendientes}"

    # H49/G6 · y las de las páginas que sirve mpvd: los `t('…')` de sus .js y el texto de sus .html
    for carpeta in ("mpvd/remote/www", "mpvd/share/www"):
        encontradas |= set(i18n.page_keys(str(ROOT / carpeta)))

    for lang in ("en", "fr"):
        cat = json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        faltan = sorted(encontradas - set(cat))
        assert not faltan, f"locales/{lang}.json: faltan {len(faltan)} cadenas, p. ej. {faltan[:5]}"
        sobran = sorted(set(cat) - encontradas)
        assert not sobran, f"locales/{lang}.json: {len(sobran)} que ya no están en el código: {sobran[:5]}"


def test_la_preferencia_gana_al_idioma_del_sistema(tmp_path):
    """H49/G2 · alguien con el sistema en inglés puede querer el reproductor en castellano. La preferencia la guarda
    mu-prefs en prefs.json, que es plano, así que el lanzador la lee antes de arrancar mpv."""
    datos = tmp_path / "data"
    datos.mkdir()
    (datos / "prefs.json").write_text(json.dumps({"_version": 1, "mu-menu": {"lang": "fr"}}), encoding="utf-8")
    args = _con_locale(tmp_path, "en_US.UTF-8", MPV_UOS_DATA_DIR=str(datos))
    assert "--script-opts-append=mu-core-lang=fr" in args, args

    # un valor con el que no se puede hacer nada no deja el reproductor a medias: se vuelve al del sistema
    (datos / "prefs.json").write_text(json.dumps({"mu-menu": {"lang": "klingon"}}), encoding="utf-8")
    args = _con_locale(tmp_path, "es_ES.UTF-8", MPV_UOS_DATA_DIR=str(datos))
    assert "--script-opts-append=mu-core-lang=es" in args, args

    # y un prefs.json roto tampoco: se ignora sin ruido
    (datos / "prefs.json").write_text('{"mu-menu": {"lang"', encoding="utf-8")
    args = _con_locale(tmp_path, "fr_FR.UTF-8", MPV_UOS_DATA_DIR=str(datos))
    assert "--script-opts-append=mu-core-lang=fr" in args, args


# -- H49/G6 · las páginas servidas ------------------------------------------------------------------------------

PAGINAS = (("mpvd/remote/www", "index.html"), ("mpvd/remote/www", "downloads.html"), ("mpvd/share/www", "room.html"))


@pytest.mark.parametrize(("carpeta", "pagina"), PAGINAS)
def test_cada_pagina_servida_carga_su_i18n_antes_de_su_javascript(carpeta, pagina):
    """Cada página tiene que cargar el `i18n.js` que le da el servidor —y **antes** que su propio .js, que llama a
    `t()`—. Va en un fichero aparte porque la sala se sirve con `script-src 'self'`, que bloquea el `<script>` en
    línea sin avisar: la página se quedaba muda y sin traducir (ADR-107)."""
    import re as _re

    html = (ROOT / carpeta / pagina).read_text(encoding="utf-8")
    assert "{/*i18n*/}" not in html, "el catálogo ya no se mete dentro del HTML"
    fuentes = _re.findall(r'<script src="([^"]+)"', html)
    assert fuentes, "la página no carga ningún .js"
    assert fuentes[0].endswith("/i18n.js"), f"i18n.js tiene que ser el primero: {fuentes}"


def test_la_plantilla_del_i18n_de_las_paginas_esta_completa():
    """Una sola copia para las tres páginas (`mpvd/i18n_page.js`): el `t()` con sus `%s` y el paso que traduce el
    texto que ya viene escrito en el HTML."""
    js = (ROOT / "mpvd" / "i18n_page.js").read_text(encoding="utf-8")
    assert "function t(s)" in js and "/%s/g" in js, "falta el t()"
    assert "createTreeWalker" in js, "falta el paso que traduce el HTML ya escrito"
    for atributo in ("placeholder", "title", "aria-label", "alt"):
        assert atributo in js, atributo


@pytest.mark.parametrize(("carpeta", "pagina"), PAGINAS)
def test_el_i18n_que_se_sirve_lleva_el_catalogo_delante(carpeta, pagina):
    """Lo que se sirve es el catálogo de esa página y, detrás, la plantilla. En castellano, un catálogo vacío."""
    i18n.catalogue.cache_clear()
    js = i18n.page_script(ROOT / carpeta, "fr-FR,fr;q=0.9").decode("utf-8")
    assert js.startswith("window.MU_T = {\"")
    assert "function t(s)" in js and "createTreeWalker" in js
    cat = json.loads(js.split("window.MU_T = ", 1)[1].split(";\n", 1)[0])
    assert cat and all(v.strip() for v in cat.values())
    vacio = i18n.page_script(ROOT / carpeta, "es-ES,es").decode("utf-8")
    assert vacio.startswith("window.MU_T = {};")


def test_todo_lo_que_llama_a_t_lo_importa():
    """Un módulo que llama a `t(...)` sin importarlo no falla al arrancar: falla **cuando hay que decir algo**, que
    es el peor momento. Pasó dos veces al ampliar el extractor (`remote/http.py` y `remote/downloads.py`), y lo
    descubrió la pasada completa con cinco tests caídos; esto lo caza en un segundo."""
    import re as _re

    llama = _re.compile(r'(?<![A-Za-z0-9_.])t\(\s*["f]')
    malos = []
    for f in sorted((ROOT / "mpvd").rglob("*.py")):
        if f.name == "i18n.py":
            continue
        src = f.read_text(encoding="utf-8")
        if llama.search(src) and "from mpvd.i18n import" not in src:
            malos.append(str(f.relative_to(ROOT)))
    assert not malos, f"llaman a t() sin importarlo: {malos}"


def test_ninguna_funcion_de_mpvd_tapa_la_funcion_t():
    """El mismo fallo que en el JavaScript, y en Python es peor: una sola asignación `t = …` (o un `for t in …`)
    hace que `t` sea local de **toda** la función, así que el `t("…")` de antes revienta con UnboundLocalError y
    el mensaje de error se convierte en un error distinto. Pasó de verdad en `asr.status`, `asr.segments` y en el
    idioma de origen de los subtítulos: lo cazó la pasada completa, no el lint."""
    import ast

    anida = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
             ast.GeneratorExp)

    def propios(nodo):
        """El ámbito de esta función, sin entrar en los anidados (que tienen el suyo)."""
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, anida):
                continue
            yield hijo
            yield from propios(hijo)

    def ata(nodo):
        out = {a.arg for a in list(nodo.args.args) + list(nodo.args.kwonlyargs) + list(nodo.args.posonlyargs)}
        for n in propios(nodo):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                out |= {(a.asname or a.name).split(".")[0] for a in n.names}
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
            elif isinstance(n, ast.Global):
                out -= set(n.names)
        return out

    malas = []
    for f in sorted((ROOT / "mpvd").rglob("*.py")):
        for nodo in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) or "t" not in ata(nodo):
                continue
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "t"
                   for n in propios(nodo)):
                malas.append(f"{f.relative_to(ROOT)}:{nodo.lineno} {nodo.name}()")
    assert not malas, f"funciones que atan `t` y llaman a t(): {malas}"


def test_ninguna_variable_de_las_paginas_tapa_la_funcion_t():
    """Un `var t = …` dentro de una función tapa el `t()` global en TODA la función (hoisting), así que la línea de
    antes revienta con «t is not a function» y la página se queda a medias. Pasó de verdad con la lista de
    invitados de la sala, y no se ve hasta que alguien abre esa página: aquí se ve siempre."""
    import re as _re

    tapan = _re.compile(r"\b(?:var|let|const)\s+t\s*=|function\s*\(\s*t\s*\)|\(\s*t\s*\)\s*=>")
    for carpeta in ("mpvd/remote/www", "mpvd/share/www"):
        for f in sorted((ROOT / carpeta).glob("*.js")):
            if f.name.endswith(".min.js"):
                continue
            malas = [n for n, linea in enumerate(f.read_text(encoding="utf-8").split("\n"), 1)
                     if tapan.search(linea)]
            assert not malas, f"{f.name}: líneas que tapan t(): {malas}"


def test_el_javascript_de_las_paginas_compila():
    """Un paréntesis mal puesto al envolver una cadena deja la página muda y no lo nota ningún test de Python.
    `node --check` vale como compilador; si no hay node, se omite (no es una dependencia del proyecto)."""
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("sin node")
    ficheros = [ROOT / "mpvd" / "i18n_page.js"]
    for carpeta in ("mpvd/remote/www", "mpvd/share/www"):
        ficheros += [f for f in sorted((ROOT / carpeta).glob("*.js")) if not f.name.endswith(".min.js")]
    for f in ficheros:
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"{f.name}: {r.stderr}"


def test_el_catalogo_de_la_pagina_va_en_el_idioma_del_navegador_y_solo_con_lo_suyo():
    """No se le manda el catálogo entero (1.500 cadenas, 70 KB en un móvil por 3G) sino solo lo que esa página usa,
    y en castellano no se manda nada porque la propia página ya está en castellano."""
    i18n.catalogue.cache_clear()
    for carpeta, _ in PAGINAS:
        www = ROOT / carpeta
        assert i18n.page_catalogue(www, "es-ES,es;q=0.9") == "{}"
        for lang, cabecera in (("en", "en-US,en;q=0.9"), ("fr", "fr-FR,fr;q=0.9")):
            cat = json.loads(i18n.page_catalogue(www, cabecera))
            completo = i18n.catalogue(lang)
            claves = i18n.page_keys(str(www))
            assert cat, (carpeta, lang)
            assert set(cat) <= set(claves), "no se manda nada que la página no use"
            assert len(cat) < len(completo) / 2, "se manda un trozo, no el catálogo entero"
            assert all(v.strip() for v in cat.values()), "una traducción vacía taparía el castellano"


def test_el_texto_del_html_y_del_js_esta_todo_en_el_catalogo():
    """Las dos mitades de una página: lo que pinta el JavaScript y lo que ya viene escrito en el HTML. Una frase
    suelta deja la página medio traducida, que es peor que no traducirla."""
    for carpeta, pagina in PAGINAS:
        claves = i18n.page_keys(str(ROOT / carpeta))
        delhtml = i18n.html_keys((ROOT / carpeta / pagina).read_text(encoding="utf-8"))
        assert delhtml <= claves
        for lang in ("en", "fr"):
            cat = i18n.catalogue(lang)
            faltan = sorted(k for k in claves if k not in cat)
            assert not faltan, f"{carpeta} en {lang}: {faltan[:5]}"


def test_el_mando_llega_al_movil_traducido(remote_env):  # noqa: F811
    """La prueba de verdad, por HTTP: el móvil pide la página en francés y la recibe con las cadenas en francés
    puestas dentro; pidiéndola en castellano no se le manda catálogo."""
    _h, d = remote_env
    base = d.call("remote.pair")["url"].split("/#")[0]

    def pedir(ruta: str, cabecera: str) -> str:
        r = urllib.request.Request(base + ruta, headers={"Accept-Language": cabecera})
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.read().decode("utf-8")

    html = pedir("/", "fr-FR,fr;q=0.9,en;q=0.5")
    assert 'src="/i18n.js"' in html and "Canales" in html, "la página se sirve en castellano y se traduce al cargar"

    js = pedir("/i18n.js", "fr-FR,fr;q=0.9,en;q=0.5")
    cat = json.loads(js.split("window.MU_T = ", 1)[1].split(";\n", 1)[0])
    assert cat.get("Canales") == "Chaînes" and cat.get("Más") == "Plus", "el texto del HTML va en el catálogo"
    assert cat.get("Mando") == "Télécommande" and cat.get("Buscar canal o emisora…"), "y los atributos (placeholder)"
    assert "function t(s)" in js and "createTreeWalker" in js

    assert pedir("/i18n.js", "es-ES,es;q=0.9").startswith("window.MU_T = {};")


# -- H49/G8 · los mensajes de mpvd que no son RpcError, y el idioma por petición -------------------------------

def test_los_nombres_de_los_formatos_salen_en_el_idioma_del_reproductor(daemon_env):
    """Las tablas de datos (los formatos de «Convertir» y de «Descargar», los modelos de voz, los nombres de las
    tareas) están en castellano en el código porque la cadena castellana ES la clave; se traducen al servirlas, que
    es lo único que funciona —el idioma se decide al atender la petición, no al importar el módulo—."""
    daemon_env.extra_env["LANG"] = "fr_FR.UTF-8"
    daemon_env.cli("ensure")                      # el demonio arranca ya con ese idioma
    daemon_env.wait(daemon_env.alive, timeout=30)
    presets = daemon_env.call("convert.presets")
    etiquetas = [p["label"] for p in presets["presets"]]
    assert "Plus petit (H.265)" in etiquetas, etiquetas
    assert presets["quality_labels"]["high"] == "élevée", presets["quality_labels"]
    titulos = [p["title"] for p in daemon_env.call("ytdl.presets")["presets"]]
    assert any(x.startswith("Vidéo · jusqu") for x in titulos), titulos
    modelos = {m["name"]: m["note"] for m in daemon_env.call("asr.models")["models"]}
    assert modelos["tiny"] == "très rapide, qualité basique", modelos["tiny"]


def test_lo_que_se_dice_en_una_peticion_va_en_el_idioma_de_quien_la_hace(remote_env):  # noqa: F811
    """Un error de la sala o del mando lo lee el INVITADO, no el anfitrión: su idioma es el de su navegador. Se
    resuelve con una variable de contexto que fija cada petición, así que no hay que arrastrar el idioma por veinte
    funciones hasta el `raise`."""
    _h, d = remote_env
    base = d.call("remote.pair")["url"].split("/#")[0]

    def error(cabecera: str) -> str:
        req = urllib.request.Request(base + "/api/state", headers={"Accept-Language": cabecera})
        try:
            urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read()).get("error", "")
        raise AssertionError("tenía que dar 401: no está emparejado")

    assert "QR" in error("es-ES,es") and "empareja" in error("es-ES,es").lower()
    assert error("fr-FR,fr;q=0.9").startswith("non associé"), error("fr-FR,fr;q=0.9")
    assert error("en-US,en").startswith("not paired"), error("en-US,en")
    # y el idioma de una petición no se queda pegado para la siguiente
    assert "empareja" in error("es-ES,es").lower()
