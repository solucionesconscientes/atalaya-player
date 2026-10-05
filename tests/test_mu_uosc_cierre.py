"""`mu.uosc` no puede creerse `menu/type` mientras tiene un cierre en vuelo (H63/N2, ADR-122).

Entre «le pido a uosc que cierre mi menú» y que lo cierre pasan milisegundos (21 medidos con el equipo cargado).
Quien pregunte en ese hueco lee un menú que ya está muerto: el módulo mandaba `update-menu` en vez de `open-menu`
y uosc atendía el cierre después, dejándolo con su navegación en pie y sin menú en pantalla (1 de cada 10 pasadas
de `test_menu_create_guests_permissions_close`).

El invariante es pequeño y exacto, así que se prueba con LuaJIT y un `mp` de pega en vez de a través de una
carrera: es la primera prueba unitaria de Lua del proyecto y el arnés sirve para las que vengan.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULOS = ROOT / "mpv-config" / "script-modules"

# El `mp` de pega: propiedades en una tabla, observadores que se avisan al escribir, y un reloj que se mueve a mano.
ARNES = """
local propiedades, observadores, enviados = {}, {}, {}
local reloj = 1000.0

package.preload['mp'] = function()
  return {
    get_property_native = function(n) return propiedades[n] end,
    set_property_native = function(n, v) propiedades[n] = v end,
    commandv = function(...) enviados[#enviados + 1] = { ... } end,
    get_time = function() return reloj end,
    get_script_name = function() return 'prueba' end,
    observe_property = function(n, _, fn)
      observadores[n] = observadores[n] or {}
      table.insert(observadores[n], fn)
    end,
  }
end
package.preload['mp.utils'] = function()
  return { format_json = function() return '{}' end, parse_json = function() return nil end }
end
package.preload['mp.msg'] = function()
  return { warn = function() end, error = function() end, info = function() end, debug = function() end }
end

-- lo que uosc publica: escribe la propiedad Y avisa a quien la observa, como hace mpv
local function publica(valor)
  propiedades['user-data/uosc/menu/type'] = valor
  for _, fn in ipairs(observadores['user-data/uosc/menu/type'] or {}) do fn('user-data/uosc/menu/type', valor) end
end

local function comprueba(que, esperado, obtenido)
  if esperado ~= obtenido then
    print('FALLO: ' .. que .. ' esperaba ' .. tostring(esperado) .. ' y da ' .. tostring(obtenido))
    os.exit(1)
  end
end
"""


def _corre(cuerpo: str) -> str:
    luajit = shutil.which("luajit")
    if not luajit:
        pytest.skip("sin luajit")
    guion = ARNES + f"local uosc = require('mu.uosc')\n{cuerpo}\nprint('ok')\n"
    res = subprocess.run(
        [luajit, "-e", f'package.path = "{MODULOS}/?.lua;" .. package.path', "-"],
        input=guion, capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.strip()


def test_un_cierre_en_vuelo_no_se_lee_como_menu_abierto() -> None:
    """Lo esencial: tras pedir el cierre, `open_type()` no puede seguir diciendo que ese menú está abierto."""
    assert _corre("""
      publica('mu-share')
      comprueba('menú abierto', 'mu-share', uosc.open_type())
      uosc.close('mu-share')
      comprueba('cierre en vuelo', nil, uosc.open_type())
      publica(nil)                       -- uosc lo cierra de verdad
      comprueba('ya cerrado', nil, uosc.open_type())
    """).endswith("ok")


def test_abrir_despues_de_cerrar_vuelve_a_valer() -> None:
    """uosc atiende los mensajes en orden: si tras el cierre pedimos abrir, lo que queda es abierto."""
    assert _corre("""
      publica('mu-share')
      uosc.close('mu-share')
      uosc.open({ type = 'mu-share', items = {} })
      comprueba('reabierto', 'mu-share', uosc.open_type())
    """).endswith("ok")


def test_el_cierre_de_otro_menu_no_tapa_el_nuestro() -> None:
    """Cerrar «mu-share-request» no puede hacer invisible el menú «mu-share» que haya abierto."""
    assert _corre("""
      publica('mu-share')
      uosc.close('mu-share-request')
      comprueba('otro menú', 'mu-share', uosc.open_type())
    """).endswith("ok")


def test_si_uosc_no_contesta_se_vuelve_a_creer_lo_que_publica() -> None:
    """El mismo seguro que `asking`: pasado un segundo sin respuesta, lo publicado manda (uosc puede no estar)."""
    assert _corre("""
      publica('mu-share')
      uosc.close('mu-share')
      comprueba('en vuelo', nil, uosc.open_type())
      reloj = reloj + 1.5
      comprueba('pasado el seguro', 'mu-share', uosc.open_type())
    """).endswith("ok")


def test_el_menu_es_mio_tambien_en_el_hueco_de_confirmacion() -> None:
    """H63 · `mine()`: lo que hay que preguntar antes de TIRAR trabajo.

    Entre «le pido el menú a uosc» y que lo confirme pasan de 1 a 27 ms (ADR-112). `mu-recap` miraba
    `open_type() ~= MENU` para decidir si seguía habiendo alguien esperando el resumen, así que si la respuesta de
    mpvd caía en ese hueco **tiraba el resumen** y volvía a «idle»: pedías «¿Qué me he perdido?» y no salía nada.
    """
    assert _corre("""
      publica('mu-recap')
      comprueba('el abierto es mío', true, uosc.mine('mu-recap'))
      publica(nil)
      comprueba('sin menú, no es mío', false, uosc.mine('mu-recap'))
      uosc.open({ type = 'mu-recap', items = {} })        -- pedido, uosc aún no ha contestado
      comprueba('pedido y sin confirmar: mío', true, uosc.mine('mu-recap'))
      publica('mu-recap')
      comprueba('confirmado: mío', true, uosc.mine('mu-recap'))
    """).endswith("ok")


def test_el_menu_de_otro_no_es_mio_y_el_seguro_tambien_vale_aqui() -> None:
    assert _corre("""
      publica('mu-share')
      comprueba('el de otro no es mío', false, uosc.mine('mu-recap'))
      uosc.open({ type = 'mu-recap', items = {} })
      comprueba('pedido el mío: mío', true, uosc.mine('mu-recap'))
      reloj = reloj + 1.5                                  -- uosc no contesta: manda lo publicado
      comprueba('pasado el seguro', false, uosc.mine('mu-recap'))
    """).endswith("ok")


def test_si_lo_cierro_yo_deja_de_ser_mio() -> None:
    """Lo contrario también importa: si cerramos nuestro menú a propósito, lo que llegue tarde NO debe reabrirlo."""
    assert _corre("""
      publica('mu-recap')
      uosc.close('mu-recap')
      comprueba('cerrado por nosotros', false, uosc.mine('mu-recap'))
    """).endswith("ok")
