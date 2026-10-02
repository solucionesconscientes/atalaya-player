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
