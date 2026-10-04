"""Job queue: priority order, cancellation, progress, failures and guardian throttling."""

import asyncio

import pytest

from mpvd.guardian import PerformanceGuardian
from mpvd.jobs import JobQueue, Priority, Status


def run(coro):
    return asyncio.run(coro)


def test_priority_order_with_single_worker():
    async def go():
        q = JobQueue(workers=1)
        order = []
        gate = asyncio.Event()

        async def blocker(job):
            await gate.wait()

        async def body(job):
            order.append(job.name)

        q.submit("block", blocker)
        await q.start()
        await asyncio.sleep(0.05)  # blocker is running; the rest queue up
        q.submit("index", body, priority="index")
        q.submit("pre", body, priority=Priority.PRECOMPUTE)
        q.submit("urgent", body, priority=0)
        q.submit("inter", body, priority="INTERACTIVE")
        q.submit("inter2", body, priority="interactive")
        gate.set()
        for _ in range(50):
            if len(order) == 5:
                break
            await asyncio.sleep(0.02)
        await q.stop()
        return order

    assert run(go()) == ["urgent", "inter", "inter2", "pre", "index"]


def test_progress_result_failure_and_cancel():
    async def go():
        q = JobQueue(workers=2)
        await q.start()

        async def ok(job):
            job.report(0.5, "half")
            await asyncio.sleep(0.01)
            return {"x": 1}

        async def bad(job):
            raise RuntimeError("nope")

        async def forever(job):
            await asyncio.sleep(60)

        j1, j2, j3 = q.submit("ok", ok), q.submit("bad", bad), q.submit("slow", forever)
        await j1.wait(5)
        await j2.wait(5)
        await asyncio.sleep(0.05)
        assert q.cancel(j3.id) is True
        await j3.wait(5)
        assert q.cancel(j3.id) is False
        assert q.cancel("missing") is False
        listing = {j["id"]: j for j in q.list()}
        await q.stop()
        return j1, j2, j3, listing

    j1, j2, j3, listing = run(go())
    assert j1.status is Status.DONE and j1.result == {"x": 1} and j1.progress == 1.0 and j1.message == "half"
    assert j2.status is Status.FAILED and "RuntimeError: nope" in (j2.error or "")
    assert j3.status is Status.CANCELLED
    assert listing[j1.id]["status"] == "done" and listing[j3.id]["status"] == "cancelled"


def test_cancel_queued_job_and_session_cleanup():
    async def go():
        q = JobQueue(workers=1)
        gate = asyncio.Event()

        async def blocker(job):
            await gate.wait()

        async def body(job):
            return 1

        await q.start()
        q.submit("block", blocker, session_id="s1")
        await asyncio.sleep(0.02)
        queued = q.submit("queued", body, session_id="s1")
        other = q.submit("other", body, session_id="s2")
        assert q.cancel(queued.id) is True
        assert queued.status is Status.CANCELLED
        n = q.cancel_session("s1")
        gate.set()
        await other.wait(5)
        await q.stop()
        return n, other.status

    n, status = run(go())
    assert n == 1 and status is Status.DONE


def test_heavy_jobs_wait_while_throttled():
    async def go():
        now = [1000.0]
        g = PerformanceGuardian(drop_rate_threshold=2.0, cooldown=0.3, clock=lambda: now[0])
        q = JobQueue(workers=2, guardian=g)
        await q.start()
        ran = []

        async def body(job):
            ran.append(job.name)

        g.observe("s", 0)
        now[0] += 1.0
        g.observe("s", 100)  # 100 drops/s -> throttled for 0.3 s of fake time
        assert g.throttled
        heavy = q.submit("heavy", body, heavy=True, priority="precompute")
        light = q.submit("light", body, heavy=False, priority="index")
        await light.wait(5)
        await asyncio.sleep(0.05)
        assert heavy.status is Status.QUEUED and ran == ["light"]
        now[0] += 1.0  # cooldown over in fake time
        g.release()  # notify listeners (a real clock would wake via the timer)
        await heavy.wait(5)
        await q.stop()
        return ran

    assert run(go()) == ["light", "heavy"]


def test_guardian_rate_and_status():
    now = [0.0]
    g = PerformanceGuardian(drop_rate_threshold=2.0, cooldown=5.0, clock=lambda: now[0])
    events = []
    g.add_listener(events.append)
    assert g.observe("a", 0) is False
    now[0] = 1.0
    assert g.observe("a", 1) is False  # 1 drop/s: fine
    now[0] = 2.0
    assert g.observe("a", 10) is True  # 9 drops/s: throttle
    assert events == [True] and g.status()["triggers"] == 1
    now[0] = 6.9
    assert g.throttled
    now[0] = 7.1
    assert not g.throttled
    assert g.observe("a", 10, paused=True) is False  # paused sessions are ignored
    events.clear()
    now[0] = 8.0
    g.observe("a", 10)
    now[0] = 9.0
    g.observe("a", 100)
    g.release()
    assert events == [True, False]
    g.forget("a")
    assert g.status()["sessions"] == []


def test_priority_parse_rejects_garbage():
    with pytest.raises(KeyError):
        Priority.parse("whatever")


def test_mientras_se_ve_algo_no_arranca_nada_especulativo():
    """H70 · la regla que pidió Ser: un reproductor no hace nada que no le hayan pedido mientras ves una película.

    URGENT (un subtítulo que va a salir ya) e INTERACTIVE (lo que has pulsado) siguen; PRECOMPUTE e INDEX
    —analizar la intro al abrir, pre-subtitular el siguiente episodio, indexar la biblioteca— esperan a que se
    pause o se pare. Antes solo había un guardián que reaccionaba DESPUÉS de perder fotogramas: el tirón se veía."""
    async def go():
        viendo = {"si": True}
        q = JobQueue(workers=1, playing=lambda: viendo["si"])
        await q.start()
        hechos: list[str] = []

        async def body(job):
            hechos.append(job.name)

        intro = q.submit("intro.analyze", body, priority=Priority.PRECOMPUTE, heavy=True)
        indice = q.submit("library.index", body, priority=Priority.INDEX, heavy=True)
        pedido = q.submit("recap.ask", body, priority=Priority.INTERACTIVE)
        urgente = q.submit("asr.prepare", body, priority=Priority.URGENT)

        await pedido.wait(5)
        await urgente.wait(5)
        await asyncio.sleep(0.1)
        assert sorted(hechos) == ["asr.prepare", "recap.ask"], hechos
        assert intro.status is Status.QUEUED and indice.status is Status.QUEUED

        viendo["si"] = False       # se pausa o se para: lo que esperaba arranca solo
        q.wake()
        await intro.wait(5)
        await indice.wait(5)
        assert sorted(hechos) == ["asr.prepare", "intro.analyze", "library.index", "recap.ask"], hechos
        await q.stop()

    run(go())
