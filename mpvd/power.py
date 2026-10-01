"""Despertar para grabar y apagar al terminar (H40).

Lo que se puede hacer sin pedir contraseña y lo que no, comprobado en este equipo el 2026-10-01:

* **Suspender y apagar**: sí. logind contesta `yes` a `CanSuspend` y `CanPowerOff` para la sesión local, así que
  `systemctl suspend` / `systemctl poweroff` funcionan sin sudo.
* **Poner el despertador (RTC)**: no. `rtcwake` da «/dev/rtc0: Permiso denegado» y `sudo -n` pide contraseña. El
  despertador necesita **una** regla de sudoers puesta una vez a mano (F4): el programa la detecta, no la instala, y
  dice la orden exacta en vez de fallar en silencio. Lo mismo en macOS con `pmset schedule wake`.
* **No dormirse mientras graba**: `systemd-inhibit --what=sleep:idle` envuelve al ffmpeg de la grabación (en macOS
  `caffeinate -i`, en Windows la bandera `ES_SYSTEM_REQUIRED` de `SetThreadExecutionState`).

Antes de suspender o apagar se comprueban **tres seguros** (F3) y se avisa 60 s con un botón de *Cancelar*: si alguien
está usando el equipo, si hay una grabación cerca o si queda algo descargando o convirtiendo, no se hace y se dice por
qué. Apagar el ordenador de alguien por error es de las pocas cosas que no se pueden deshacer.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

from mpvd import notify
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer

log = logging.getLogger("mpvd.power")

ACTIONS = ("nothing", "suspend", "shutdown")
WAKE_MARGIN = 300.0        # el despertador se pone 5 min antes de la hora de la grabación
NOTICE_SECONDS = 60.0      # aviso cancelable antes de suspender o apagar
RECORDING_MARGIN = 900.0   # una grabación a menos de 15 min impide suspender o apagar
TASK_NAME = "MPV-UOS-Wake"

# Lo que hay que ejecutar UNA vez con sudo para que el despertador funcione sin contraseña (F4). No se ejecuta aquí.
SUDOERS_HINT_LINUX = (
    "echo \"$USER ALL=(root) NOPASSWD: /usr/sbin/rtcwake\" | sudo tee /etc/sudoers.d/mpv-uos-rtcwake"
    " && sudo chmod 0440 /etc/sudoers.d/mpv-uos-rtcwake"
)
SUDOERS_HINT_MACOS = (
    "echo \"$USER ALL=(root) NOPASSWD: /usr/bin/pmset\" | sudo tee /etc/sudoers.d/mpv-uos-pmset"
    " && sudo chmod 0440 /etc/sudoers.d/mpv-uos-pmset"
)


def _fake() -> str:
    """Programa que sustituye a todas las órdenes de energía (tests): recibe la orden entera como argumentos."""
    return os.environ.get("MPVD_POWER_FAKE", "")


async def _run(args: list[str], timeout: float = 20.0) -> tuple[int, str]:
    fake = _fake()
    if fake:
        args = [fake, *args]
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return 124, "tardó demasiado"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


def _logind_can(what: str) -> bool:
    """``CanSuspend`` / ``CanPowerOff`` de logind: 'yes' = se puede sin contraseña."""
    busctl = shutil.which("busctl")
    if busctl is None:
        return False
    try:
        out = subprocess.run([busctl, "call", "org.freedesktop.login1", "/org/freedesktop/login1",
                              "org.freedesktop.login1.Manager", what],
                             capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return '"yes"' in out


def inhibit_prefix(reason: str) -> list[str]:
    """Con qué envolver un proceso para que el equipo no se duerma mientras dura (F2). Vacío si no hay con qué."""
    if _fake():
        return []
    if sys.platform == "darwin":
        caffeinate = shutil.which("caffeinate")
        return [caffeinate, "-i"] if caffeinate else []
    if sys.platform == "win32":
        return []      # en Windows lo hace SetThreadExecutionState desde el propio proceso (ver docs/PLATAFORMAS.md)
    inhibit = shutil.which("systemd-inhibit")
    if inhibit is None:
        return []
    return [inhibit, "--what=sleep:idle", "--who=MPV-UOS", f"--why={reason}", "--mode=block"]


class PowerService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.wake_at: float | None = None
        self.pending: dict[str, Any] | None = None     # acción armada tras una grabación
        self._notice: asyncio.Task[None] | None = None

    # -- qué se puede hacer aquí ----------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        fake = bool(_fake())
        plat = sys.platform
        caps: dict[str, Any] = {"platform": plat, "fake": fake, "actions": list(ACTIONS),
                                "wake_margin": WAKE_MARGIN, "notice_seconds": NOTICE_SECONDS,
                                "recording_margin": RECORDING_MARGIN}
        if fake:
            caps.update({"can_suspend": True, "can_shutdown": True, "can_wake": True, "wake_tool": "fake",
                         "inhibit": True, "install_hint": "", "reason": ""})
            return caps
        if plat == "win32":
            tool = shutil.which("schtasks")
            caps.update({"can_suspend": True, "can_shutdown": True, "can_wake": tool is not None,
                         "wake_tool": "schtasks" if tool else "", "inhibit": True, "install_hint": "",
                         "reason": "" if tool else "falta schtasks"})
            return caps
        if plat == "darwin":
            pmset = shutil.which("pmset")
            ok = pmset is not None and _sudo_ok(["pmset", "-g", "sched"])
            caps.update({"can_suspend": shutil.which("pmset") is not None,
                         "can_shutdown": shutil.which("osascript") is not None,
                         "can_wake": ok, "wake_tool": "pmset" if pmset else "",
                         "inhibit": shutil.which("caffeinate") is not None,
                         "install_hint": "" if ok else SUDOERS_HINT_MACOS,
                         "reason": "" if ok else "«pmset schedule wake» necesita sudo sin contraseña"})
            return caps
        rtcwake = shutil.which("rtcwake") or shutil.which("/usr/sbin/rtcwake")
        ok = rtcwake is not None and (os.geteuid() == 0 or _sudo_ok([rtcwake, "--version"]))
        caps.update({
            "can_suspend": _logind_can("CanSuspend"), "can_shutdown": _logind_can("CanPowerOff"),
            "can_wake": ok, "wake_tool": "rtcwake" if rtcwake else "",
            "inhibit": shutil.which("systemd-inhibit") is not None,
            "install_hint": "" if ok else SUDOERS_HINT_LINUX,
            "reason": "" if ok else ("falta rtcwake" if rtcwake is None
                                     else "«rtcwake» necesita sudo sin contraseña (una sola vez)"),
        })
        return caps

    # -- el despertador -------------------------------------------------------------------------

    async def schedule_wake(self, at: float) -> dict[str, Any]:
        """Pone el despertador del equipo para ``at`` (epoch). No suspende nada: solo deja puesta la alarma."""
        caps = self.capabilities()
        if not caps["can_wake"]:
            raise RpcError(UNAVAILABLE, caps["reason"] or "este equipo no puede programar el despertador",
                           {"install_hint": caps["install_hint"]})
        when = float(at)
        if when <= time.time() + 60:
            raise RpcError(INVALID_PARAMS, "esa hora ya ha pasado (o es dentro de menos de un minuto)")
        args = self._wake_args(when)
        code, out = await _run(args)
        if code != 0:
            raise RpcError(UNAVAILABLE, f"no se pudo poner el despertador: {out[:200]}")
        self.wake_at = when
        return {"wake_at": when, "tool": caps["wake_tool"], "output": out[:200]}

    def _wake_args(self, when: float) -> list[str]:
        local = dt.datetime.fromtimestamp(when)
        if sys.platform == "darwin":
            return ["sudo", "-n", "pmset", "schedule", "wake", local.strftime("%m/%d/%y %H:%M:%S")]
        if sys.platform == "win32":
            # /xml es la única forma de pedir WakeToRun; se escribe un XML temporal junto a los datos
            return ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(self._windows_task_xml(local)), "/f"]
        rtcwake = shutil.which("rtcwake") or "/usr/sbin/rtcwake"
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        return [*prefix, rtcwake, "-m", "no", "-t", str(int(when))]

    def _windows_task_xml(self, local: dt.datetime) -> Any:
        from pathlib import Path  # noqa: PLC0415

        path = Path(self.server.settings.data_dir) / "wake-task.xml"
        body = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><TimeTrigger><StartBoundary>{local.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
    <Enabled>true</Enabled></TimeTrigger></Triggers>
  <Settings><WakeToRun>true</WakeToRun><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries></Settings>
  <Actions><Exec><Command>cmd.exe</Command><Arguments>/c exit</Arguments></Exec></Actions>
</Task>"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-16")
        return path

    async def cancel_wake(self) -> dict[str, Any]:
        """Quita el despertador. En Linux se pone la alarma «a 0», que es como se borra."""
        if sys.platform == "darwin":
            args = ["sudo", "-n", "pmset", "schedule", "cancelall"]
        elif sys.platform == "win32":
            args = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
        else:
            rtcwake = shutil.which("rtcwake") or "/usr/sbin/rtcwake"
            prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
            args = [*prefix, rtcwake, "-m", "disable"]
        code, out = await _run(args)
        self.wake_at = None
        return {"cancelled": code == 0, "output": out[:200]}

    # -- los tres seguros -----------------------------------------------------------------------

    def blockers(self, margin: float = RECORDING_MARGIN) -> list[str]:
        """Por qué NO se puede suspender o apagar ahora mismo. Lista vacía = se puede."""
        out: list[str] = []
        sessions = [s for s in self.server.sessions.all() if s.connected]
        if sessions:
            out.append("el reproductor está abierto" if len(sessions) == 1
                       else f"hay {len(sessions)} reproductores abiertos")
        now = time.time()
        for rec in self.server.schedule.items.values():
            if rec.status == "recording":
                out.append("hay una grabación en marcha")
                break
            if rec.status == "scheduled" and 0 <= rec.begin - now <= margin:
                out.append("hay una grabación a menos de " + f"{int(margin / 60)} min")
                break
        pending = self.server.pending.summary()
        if pending["downloads"]:
            out.append(f"{pending['downloads']} descarga(s) sin terminar")
        if pending["converts"]:
            out.append(f"{pending['converts']} conversión(es) sin terminar")
        if pending["subs"]:
            out.append("se están haciendo subtítulos")
        return out

    # -- suspender / apagar ---------------------------------------------------------------------

    async def _do(self, action: str) -> tuple[int, str]:
        if sys.platform == "darwin":
            args = ["pmset", "sleepnow"] if action == "suspend" else ["osascript", "-e", 'tell app "System Events" to shut down']
        elif sys.platform == "win32":
            args = (["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"] if action == "suspend"
                    else ["shutdown", "/s", "/t", "0"])
        else:
            args = ["systemctl", "suspend" if action == "suspend" else "poweroff"]
        return await _run(args, timeout=60.0)

    async def run_action(self, action: str, notice: float | None = None, force: bool = False) -> dict[str, Any]:
        """Suspende o apaga tras los tres seguros y un aviso cancelable. ``force`` se salta el aviso, nunca los seguros."""
        if action not in ACTIONS:
            raise RpcError(INVALID_PARAMS, f"acción desconocida {action!r} ({', '.join(ACTIONS)})")
        if action == "nothing":
            return {"done": False, "action": action, "blockers": []}
        caps = self.capabilities()
        if action == "suspend" and not caps["can_suspend"]:
            raise RpcError(UNAVAILABLE, "este equipo no deja suspender desde el programa")
        if action == "shutdown" and not caps["can_shutdown"]:
            raise RpcError(UNAVAILABLE, "este equipo no deja apagar desde el programa")
        blockers = self.blockers()
        if blockers:
            return {"done": False, "action": action, "blockers": blockers}
        seconds = NOTICE_SECONDS if notice is None else float(notice)
        if seconds > 0 and not force:
            texto = ("Se va a suspender el equipo" if action == "suspend" else "Se va a apagar el equipo")
            answer = await notify.ask(texto, f"La grabación ha terminado. {int(seconds)} s para cancelar.",
                                      {"cancelar": "Cancelar"}, timeout=seconds)
            if answer == "cancelar":
                return {"done": False, "action": action, "blockers": [], "cancelled": True}
        # los seguros se vuelven a mirar: en un minuto puede haber vuelto alguien
        blockers = self.blockers()
        if blockers:
            return {"done": False, "action": action, "blockers": blockers}
        code, out = await self._do(action)
        return {"done": code == 0, "action": action, "blockers": [], "output": out[:200]}

    # -- lo que se hace al acabar una grabación -------------------------------------------------

    def arm(self, action: str, recording_id: str = "") -> dict[str, Any]:
        if action not in ACTIONS:
            raise RpcError(INVALID_PARAMS, f"acción desconocida {action!r}")
        self.pending = None if action == "nothing" else {"action": action, "recording": recording_id}
        return self.status()

    async def on_recording_finished(self, recording_id: str = "") -> dict[str, Any] | None:
        """Lo llama el programador cuando una grabación termina."""
        armed = self.pending
        if not armed:
            return None
        if armed.get("recording") and recording_id and armed["recording"] != recording_id:
            return None
        self.pending = None
        return await self.run_action(armed["action"])

    def status(self) -> dict[str, Any]:
        return {"wake_at": self.wake_at, "pending": self.pending, "blockers": self.blockers(),
                **self.capabilities()}


def _sudo_ok(args: list[str]) -> bool:
    """¿Se puede ejecutar eso con sudo SIN contraseña? (`sudo -n`, que falla en vez de preguntar)."""
    sudo = shutil.which("sudo")
    if sudo is None:
        return False
    try:
        return subprocess.run([sudo, "-n", *args], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def register(server: MpvdServer, service: PowerService) -> None:
    d = server.dispatcher

    @d.method("power.status")
    async def status(ctx: Any) -> dict[str, Any]:
        """Qué puede hacer este equipo (suspender, apagar, despertador), qué falta para el despertador, qué impide
        ahora mismo suspender o apagar y qué hay armado para después de la grabación."""
        return service.status()

    @d.method("power.wake.schedule")
    async def wake_schedule(ctx: Any, at: float) -> dict[str, Any]:
        """Pone el despertador del equipo a esa hora (epoch). Solo despierta de la suspensión, no enciende un equipo
        apagado."""
        return await service.schedule_wake(at)

    @d.method("power.wake.cancel")
    async def wake_cancel(ctx: Any) -> dict[str, Any]:
        """Quita el despertador."""
        return await service.cancel_wake()

    @d.method("power.after")
    async def after(ctx: Any, action: str, recording: str = "") -> dict[str, Any]:
        """Qué hacer cuando acabe la grabación: nothing | suspend | shutdown."""
        return service.arm(action, recording)

    @d.method("power.run")
    async def run(ctx: Any, action: str, notice: float | None = None, force: bool = False) -> dict[str, Any]:
        """Suspende o apaga ahora, tras los tres seguros y el aviso cancelable. Devuelve `blockers` si no se hizo."""
        return await service.run_action(action, notice, force)
