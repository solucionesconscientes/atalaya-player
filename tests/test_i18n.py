"""H49 · la maquinaria de los idiomas (ADR-087, diseño en docs/IDIOMAS.md).

Lo que se comprueba aquí es lo que hace que traducir ~1.800 cadenas sea viable: que la cadena castellana sea la
clave, que lo no traducido caiga al castellano (nunca una clave cruda ni un hueco), y que el idioma se decida una
sola vez y con la regla de Ser: castellano o francés → ese; inglés o cualquier otro → inglés.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from mpvd import i18n
from tests.conftest import ROOT, start_mpv
from tests.test_mu_iptv import tv  # noqa: F401

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

    # los scripts ya traducidos (H49 va por etapas: aquí solo los que tienen `mu.i18n`)
    hechos = [p for p in sorted((ROOT / "mpv-config" / "scripts").glob("mu-*/main.lua"))
              if "require('mu.i18n')" in p.read_text(encoding="utf-8")]
    assert hechos, "ningún script usa mu.i18n todavía"
    encontradas: set[str] = set()
    for f in hechos:
        _, found, glued = ex.process(f)
        encontradas |= set(found)
        assert not glued, f"{f.name}: frases pegadas con `..`, que no se pueden traducir a trozos: {glued}"

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
