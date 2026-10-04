"""tools/comparar.py · la herramienta con la que se contesta «¿Atalaya pesa más que mpv?».

Lo que se mide aquí es lo que se hizo mal al medir de verdad y no se quiere repetir: contar mpvd (que en /proc
se llama `python`), no dejar que el orden de las aperturas favorezca a un reproductor, y no llamar «diferencia»
a algo que cabe dentro de lo que varía la máquina consigo misma."""

import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def cargar():
    spec = importlib.util.spec_from_file_location("comparar_tool", RAIZ / "tools/comparar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def medicion(total, perdidos=0, como=None, ventana="1280x720", raton=False, hilos=None):
    return {"perdidos": perdidos, "cpu": {"mpv:1": total}, "total_%": total, "mhz": 2300,
            "como": como or {k: "igual" for k in cargar().PROPS}, "ventana": ventana,
            "pantalla_completa": False, "raton_encima": raton,
            "hilos": hilos if hilos is not None else {"mpv:1": total}}


def test_el_orden_no_puede_favorecer_a_ninguno():
    """Alternando A, B, A, B el segundo de cada pareja mide siempre con la CPU más caliente: menos MHz y más
    porcentaje para el mismo trabajo. Por eso el orden es A, B, B, A, con las mismas aperturas para cada uno."""
    mod = cargar()
    assert mod.orden(1) == ["mpv", "atalaya", "atalaya", "mpv"]
    o = mod.orden(3)
    assert o.count("mpv") == o.count("atalaya") == 6
    # ninguno va siempre detrás del otro
    detras_de_mpv = sum(1 for a, b in zip(o, o[1:]) if a == "mpv" and b == "atalaya")
    detras_de_atalaya = sum(1 for a, b in zip(o, o[1:]) if a == "atalaya" and b == "mpv")
    assert detras_de_mpv == detras_de_atalaya


def test_mpvd_cuenta_como_parte_de_la_pila():
    """El intérprete del .venv se llama `python` en /proc. Con la lista de antes («python3») el demonio no salía
    en ninguna cuenta: Atalaya parecía gastar solo lo del reproductor."""
    mod = cargar()
    assert "python" in mod.INTERESA
    vivos = mod.procesos()
    assert all(isinstance(pid, int) for pid in vivos)
    assert all(not n.startswith("python") for n, _, _ in vivos.values()), "un python de la pila se llama mpvd"


def test_una_diferencia_menor_que_el_ruido_no_es_una_diferencia():
    mod = cargar()
    acum = {"mpv": [medicion(28.0), medicion(31.0)], "atalaya": [medicion(29.0), medicion(30.5)]}
    texto = mod.resumen(acum)
    assert "gastan lo mismo" in texto
    assert "Los dos descodifican igual" in texto


def test_una_diferencia_de_verdad_se_dice_con_su_signo():
    mod = cargar()
    acum = {"mpv": [medicion(28.0), medicion(28.4)], "atalaya": [medicion(34.0), medicion(34.3)]}
    texto = mod.resumen(acum)
    assert "Atalaya gasta 5.9 puntos más que mpv" in texto
    acum = {"mpv": [medicion(34.0), medicion(34.3)], "atalaya": [medicion(28.0), medicion(28.4)]}
    assert "5.9 puntos menos que mpv" in mod.resumen(acum)


def test_avisa_cuando_los_dos_no_estan_haciendo_lo_mismo():
    """Comparar un reproductor que descodifica por hardware con otro que lo hace por software no compara nada."""
    mod = cargar()
    con_hw = {k: "igual" for k in mod.PROPS} | {"hwdec-current": "vaapi"}
    sin_hw = {k: "igual" for k in mod.PROPS} | {"hwdec-current": "no"}
    acum = {"mpv": [medicion(28.0, como=con_hw)], "atalaya": [medicion(28.2, como=sin_hw)]}
    texto = mod.resumen(acum)
    assert "no están haciendo lo mismo" in texto and "hwdec-current" in texto


def test_el_gasto_se_puede_partir_por_hilo():
    """mpv nombra sus hilos (vo, demux, uno por script Lua). Mirar el proceso entero decía «Atalaya gasta más»;
    mirar por hilo fue lo que señaló a la interfaz y descartó los 23 scripts propios y el demonio."""
    mod = cargar()
    import os
    propios = mod.hilos(os.getpid())
    assert propios, "al menos el hilo principal"
    assert all(":" in k for k in propios), "cada clave es nombre:tid"
    assert all(v >= 0 for v in propios.values())
    assert mod.hilos(2 ** 30) == {}, "un pid que no existe no es un error"


def test_si_el_raton_estaba_encima_se_dice_aparte():
    """El dato que explicó por qué la misma medición salía 33 % unas veces y 27 % otras: con el puntero sobre la
    ventana, uosc mantiene su interfaz dibujándose; con el puntero fuera, Atalaya cuesta lo mismo que mpv."""
    mod = cargar()
    acum = {"mpv": [medicion(28.0), medicion(28.2)],
            "atalaya": [medicion(33.1, raton=True), medicion(27.2, raton=False)]}
    texto = mod.resumen(acum)
    assert "con el ratón encima de la ventana 33.1 %" in texto
    assert "con el ratón fuera 27.2 %" in texto
