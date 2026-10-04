"""H67 · programar en días de la semana, de forma INDEFINIDA hasta que se quite.

Lo que pidió Ser: «quiero que si se dice de l-v, cada día, etc, siga así de forma indefinida, hasta que el usuario
indicara lo contrario». Aquí se comprueba lo que eso significa de verdad: que la serie sobreviva a que la franja
termine, a que se pierda porque el equipo estaba apagado y a que alguien se salte un día; que quitarla la quite
del todo; que la hora de reloj no se mueva el día que cambia la hora; y que avisar de un solape sea un aviso y no
una prohibición, porque grabar varios canales a la vez sí cabe (cada grabación es su propio ffmpeg copiando).
"""

import asyncio
import datetime as dt
import os
import time

import pytest

from mpvd.iptv.model import Channel
from mpvd.iptv.schedule import (
    Recording,
    ScheduleService,
    next_occurrence,
    parse_when,
    repeat_days,
    repeat_label,
)
from mpvd.rpc import RpcError

SABADO = dt.datetime(2026, 10, 3, 12, 0).timestamp()      # un sábado al mediodía, para que se note el salto
os.environ.setdefault("MPV_UOS_NO_NOTIFY", "1")           # nada de avisos de escritorio en los tests


def terminar(s: ScheduleService, rec: Recording, status: str = "done") -> None:
    """`_finish` se llama siempre desde el bucle del programador, así que aquí también va dentro de uno."""
    async def body():
        s._finish(rec, status)
        await asyncio.sleep(0)        # que las tareas que lanza (aviso de escritorio) arranquen y acaben
    asyncio.run(body())


def ts(y, mo, d, h, mi=0) -> float:
    return dt.datetime(y, mo, d, h, mi).timestamp()


# -- lo que se escribe -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("texto", "repeat", "days", "primera"), [
    ("cada día 7:00 30", "daily", [], ts(2026, 10, 4, 7, 0)),
    ("todos los días 7:00 30", "daily", [], ts(2026, 10, 4, 7, 0)),
    ("de lunes a viernes 21:30 90", "weekdays", [0, 1, 2, 3, 4], ts(2026, 10, 5, 21, 30)),
    ("l-v 21:30 90", "weekdays", [0, 1, 2, 3, 4], ts(2026, 10, 5, 21, 30)),
    ("laborables 21:30 90", "weekdays", [0, 1, 2, 3, 4], ts(2026, 10, 5, 21, 30)),
    ("martes y jueves 20:00 22:00", "weekly", [1, 3], ts(2026, 10, 6, 20, 0)),
    ("lunes, miércoles y viernes 9:00 45", "weekly", [0, 2, 4], ts(2026, 10, 5, 9, 0)),
    ("los sábados 10:00 1h", "weekly", [5], ts(2026, 10, 10, 10, 0)),   # hoy es sábado y ya son las 12
    ("fines de semana 10:00 2h", "weekly", [5, 6], ts(2026, 10, 4, 10, 0)),
    ("martes a jueves 20:00 21:00", "weekly", [1, 2, 3], ts(2026, 10, 6, 20, 0)),
    # sin repetición, como siempre
    ("21:30 90", "", [], ts(2026, 10, 3, 21, 30)),
    ("mañana 9:00 1h", "", [], ts(2026, 10, 4, 9, 0)),
])
def test_la_repeticion_se_escribe_delante_de_la_hora(texto, repeat, days, primera):
    r = parse_when(texto, SABADO)
    assert "error" not in r, r
    assert r["repeat"] == repeat and r["days"] == days
    assert r["start"] == primera, dt.datetime.fromtimestamp(r["start"])


@pytest.mark.parametrize("texto", ["every day 7:00 30", "chaque jour 7:00 30", "daily 7:00 30",
                                   "tous les jours 7:00 30"])
def test_cada_dia_vale_en_los_tres_idiomas(texto):
    assert parse_when(texto, SABADO)["repeat"] == "daily"


@pytest.mark.parametrize(("texto", "days"), [
    ("weekdays 21:30 90", [0, 1, 2, 3, 4]),
    ("mon-fri 21:30 90", [0, 1, 2, 3, 4]),
    ("du lundi au vendredi 21:30 90", [0, 1, 2, 3, 4]),
    ("tuesday and thursday 20:00 60", [1, 3]),
    ("mardi et jeudi 20:00 60", [1, 3]),
    ("weekends 10:00 2h", [5, 6]),
    ("le week-end 10:00 2h", [5, 6]),
])
def test_los_dias_tambien_valen_en_los_tres_idiomas(texto, days):
    r = parse_when(texto, SABADO)
    assert "error" not in r, r
    assert repeat_days(r["repeat"], r["days"]) == days


def test_una_repeticion_sin_hora_lo_dice_y_no_adivina():
    r = parse_when("de lunes a viernes", SABADO)
    assert "error" in r and "21:30" in r["error"]


def test_la_etiqueta_se_lee_como_se_habla():
    assert repeat_label("daily") == "cada día"
    assert repeat_label("weekdays") == "de lunes a viernes"
    assert repeat_label("weekly", [5, 6]) == "fines de semana"
    assert repeat_label("weekly", [3]) == "los jueves"
    assert repeat_label("weekly", [0, 2, 4]) == "los lunes, miércoles y viernes"
    assert repeat_label("", []) == ""


# -- la cuenta de la siguiente ------------------------------------------------------------------------------------

def test_la_siguiente_es_el_proximo_dia_que_toca():
    lunes = ts(2026, 10, 5, 21, 30)
    fin = ts(2026, 10, 5, 23, 0)
    # de lunes a viernes: después del lunes, el martes
    assert next_occurrence(lunes, fin, "weekdays", [], after=fin)[0] == ts(2026, 10, 6, 21, 30)
    # y después del viernes, el lunes siguiente (se salta el fin de semana)
    viernes = ts(2026, 10, 9, 21, 30)
    assert next_occurrence(viernes, viernes + 5400, "weekdays", [], after=viernes + 5400)[0] \
        == ts(2026, 10, 12, 21, 30)
    # los jueves: siete días
    jueves = ts(2026, 10, 8, 20, 0)
    assert next_occurrence(jueves, jueves + 3600, "weekly", [3], after=jueves + 3600)[0] == ts(2026, 10, 15, 20, 0)


def test_el_dia_que_cambia_la_hora_la_pelicula_sigue_a_la_misma_hora_del_reloj():
    """En España el reloj se atrasa la madrugada del último domingo de octubre (2026: el día 25). Si la cuenta se
    hiciera sumando 86.400 segundos, «cada día a las 21:30» pasaría a ser a las 20:30. Se cuenta por calendario."""
    sabado = ts(2026, 10, 24, 21, 30)
    inicio, fin = next_occurrence(sabado, sabado + 5400, "daily", [], after=sabado + 5400)
    siguiente = dt.datetime.fromtimestamp(inicio)
    assert (siguiente.hour, siguiente.minute) == (21, 30)
    assert siguiente.date() == dt.date(2026, 10, 25)
    # y la hora de FIN también es la del reloj, no «inicio + duración»
    assert dt.datetime.fromtimestamp(fin).strftime("%H:%M") == "23:00"


def test_una_franja_que_cruza_la_medianoche_sigue_cruzandola():
    inicio, fin = ts(2026, 10, 5, 23, 30), ts(2026, 10, 6, 1, 0)
    a, b = next_occurrence(inicio, fin, "daily", [], after=fin)
    assert dt.datetime.fromtimestamp(a).strftime("%d %H:%M") == "06 23:30"
    assert dt.datetime.fromtimestamp(b).strftime("%d %H:%M") == "07 01:00"


# -- el servicio -------------------------------------------------------------------------------------------------

class FakeServer:
    """Lo justo que usa el programador: ni sesiones, ni despertador, ni cola de trabajos."""

    def __init__(self, tmp_path):
        class S:
            data_dir = tmp_path
            cache_dir = tmp_path / "cache"
        self.settings = S()

        class Sessions:
            @staticmethod
            def all():
                return []
        self.sessions = Sessions()


def servicio(tmp_path) -> ScheduleService:
    return ScheduleService(FakeServer(tmp_path), iptv=None, path=tmp_path / "iptv-schedule.json")


def canal(cid="c1", nombre="Canal 1") -> Channel:
    return Channel(id=cid, name=nombre, url="http://example.invalid/stream.m3u8", kind="tv", source="prueba")


def test_una_serie_tiene_una_sola_franja_pendiente_y_la_siguiente_llega_al_terminar(tmp_path):
    """El modelo: se guarda la REGLA, no un calendario. De cada serie hay una pendiente; la siguiente se
    materializa cuando esta acaba, así que la lista no se llena de filas futuras y la serie no se acaba nunca."""
    s = servicio(tmp_path)
    ahora = SABADO
    rec = s.add(canal(), ts(2026, 10, 3, 21, 30), ts(2026, 10, 3, 23, 0), now=ahora, repeat="daily")
    assert rec.repeat == "daily" and rec.series == rec.id
    assert len([r for r in s.items.values() if r.status == "scheduled"]) == 1

    terminar(s, rec)
    pendientes = [r for r in s.items.values() if r.status == "scheduled"]
    assert len(pendientes) == 1
    siguiente = pendientes[0]
    assert siguiente.id != rec.id and siguiente.series == rec.series
    assert siguiente.repeat == "daily"
    # la hora de reloj se mantiene y el día avanza
    assert dt.datetime.fromtimestamp(siguiente.start).strftime("%H:%M") == "21:30"
    assert siguiente.start > time.time()


def test_la_serie_sobrevive_a_que_el_equipo_estuviera_apagado(tmp_path):
    """La prueba de «indefinido»: si la franja se perdió porque nadie encendió el equipo, al arrancar mpvd no
    solo se marca perdida, se deja puesta la siguiente. Sin esto, una semana de vacaciones mata la serie."""
    s = servicio(tmp_path)
    ayer = time.time() - 36 * 3600
    rec = Recording(id="vieja", channel={"id": "c1", "name": "Canal 1", "url": "http://x.invalid/s.m3u8"},
                    title="Canal 1", start=ayer, stop=ayer + 1800, repeat="daily", series="vieja")
    s.items[rec.id] = rec
    s.recover()
    assert s.items["vieja"].status == "missed"
    pendientes = [r for r in s.items.values() if r.status == "scheduled"]
    assert len(pendientes) == 1 and pendientes[0].series == "vieja"
    assert pendientes[0].start > time.time()


def test_saltarse_un_dia_no_acaba_con_la_serie_y_quitarla_si(tmp_path):
    """Las dos cosas que una persona quiere decir, y que no son la misma: «hoy no» y «ya no más»."""
    s = servicio(tmp_path)
    rec = s.add(canal(), ts(2026, 10, 3, 21, 30), ts(2026, 10, 3, 22, 0), now=SABADO, repeat="weekdays")

    asyncio.run(s.cancel(rec.id))                       # hoy no
    assert s.items[rec.id].status == "cancelled"
    pendientes = [r for r in s.items.values() if r.status == "scheduled"]
    assert len(pendientes) == 1, "la serie sigue"
    siguiente = pendientes[0]

    asyncio.run(s.remove(siguiente.id))                 # ya no más
    assert not [r for r in s.items.values() if r.status == "scheduled"]
    assert not [r for r in s.items.values() if r.repeat]


def test_dejar_de_repetir_deja_la_franja_que_ya_estaba_puesta(tmp_path):
    s = servicio(tmp_path)
    rec = s.add(canal(), ts(2026, 10, 3, 21, 30), ts(2026, 10, 3, 22, 0), now=SABADO, repeat="daily")
    s.set_repeat(rec.id, "")
    assert s.items[rec.id].repeat == "" and s.items[rec.id].status == "scheduled"
    terminar(s, s.items[rec.id])
    assert not [r for r in s.items.values() if r.status == "scheduled"], "ya no se crea ninguna más"


def test_la_serie_rueda_indefinidamente_sin_acumular_filas_pendientes(tmp_path):
    """Sesenta ocurrencias seguidas: ni se para ni deja dos pendientes a la vez."""
    s = servicio(tmp_path)
    rec = s.add(canal(), ts(2026, 10, 3, 7, 0), ts(2026, 10, 3, 7, 30), now=SABADO, repeat="daily")
    serie, visto = rec.series, {rec.id}
    for _ in range(60):
        pendientes = [r for r in s.items.values() if r.status == "scheduled"]
        assert len(pendientes) == 1
        actual = pendientes[0]
        assert actual.series == serie and actual.id not in visto or actual.id == rec.id
        visto.add(actual.id)
        terminar(s, actual)
    assert len(visto) >= 60
    assert dt.datetime.fromtimestamp([r for r in s.items.values() if r.status == "scheduled"][0].start) \
        .strftime("%H:%M") == "07:00"


def test_dos_reproducciones_a_la_vez_avisan_pero_grabar_dos_canales_no(tmp_path):
    """Grabar varios canales al mismo tiempo cabe: cada grabación es su propio ffmpeg copiando, sin recodificar.
    Dos reproducciones a la vez no caben —hay unos altavoces—, así que se avisa; pero se avisa, no se prohíbe."""
    s = servicio(tmp_path)
    a = s.add(canal("c1", "Canal 1"), ts(2026, 10, 3, 21, 0), ts(2026, 10, 3, 22, 0), now=SABADO, mode="record")
    b = s.add(canal("c2", "Canal 2"), ts(2026, 10, 3, 21, 30), ts(2026, 10, 3, 22, 30), now=SABADO, mode="record")
    assert s.overlapping(a) == [] and s.overlapping(b) == [], "dos grabaciones a la vez no se estorban"

    c = s.add(canal("c3", "Canal 3"), ts(2026, 10, 3, 21, 0), ts(2026, 10, 3, 22, 0), now=SABADO, mode="play")
    d = s.add(canal("c4", "Canal 4"), ts(2026, 10, 3, 21, 30), ts(2026, 10, 3, 22, 30), now=SABADO, mode="play")
    assert [r.id for r in s.overlapping(d)] == [c.id]
    assert d.status == "scheduled", "el aviso no impide programarla"
    # y una que no pisa a nadie no avisa
    e = s.add(canal("c5", "Canal 5"), ts(2026, 10, 3, 23, 0), ts(2026, 10, 3, 23, 30), now=SABADO, mode="play")
    assert s.overlapping(e) == []


def test_una_regla_sin_dias_no_se_acepta(tmp_path):
    s = servicio(tmp_path)
    with pytest.raises(RpcError):
        s.add(canal(), ts(2026, 10, 3, 21, 0), ts(2026, 10, 3, 22, 0), now=SABADO, repeat="weekly", days=[])
    with pytest.raises(RpcError):
        s.add(canal(), ts(2026, 10, 3, 21, 0), ts(2026, 10, 3, 22, 0), now=SABADO, repeat="cuando-sea")


def test_la_regla_se_guarda_en_el_disco_y_se_relee(tmp_path):
    """Si la repetición no sobrevive a cerrar mpvd, no es indefinida: es hasta que se cierre el programa."""
    s = servicio(tmp_path)
    rec = s.add(canal(), ts(2026, 10, 3, 21, 30), ts(2026, 10, 3, 22, 0), now=SABADO, repeat="weekly", days=[1, 3])
    otro = servicio(tmp_path)
    leida = otro.items[rec.id]
    assert leida.repeat == "weekly" and leida.days == [1, 3] and leida.series == rec.series
    assert leida.public()["repeat_label"] == "los martes y jueves"
