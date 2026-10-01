"""Desktop notifications, best effort and never fatal (H36/C8).

One place for the two things the player needs to say out of its own window: «la grabación ha terminado» (H21) and
«sigo con los subtítulos aunque hayas cerrado» with a *Parar* button. Actions are only available where the helper
supports them (``notify-send -A`` with libnotify ≥ 0.7.10 and a notification daemon that implements ``actions``): we
ask, and if nothing comes back the notice was just a notice. macOS ``osascript`` has no buttons, so there the notice
goes out without them. Silent with ``MPV_UOS_NO_NOTIFY`` (tests) or when no helper exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys

ICON = "media-record"


def available() -> bool:
    if os.environ.get("MPV_UOS_NO_NOTIFY"):
        return False
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    return shutil.which("notify-send") is not None


def actions_available() -> bool:
    """Buttons only on the libnotify path: ``osascript`` notifications cannot carry them."""
    return available() and sys.platform != "darwin"


async def notify(title: str, body: str, icon: str = ICON) -> None:
    """Fire and forget; waits for the helper only so it does not leak a process."""
    if not available():
        return
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", f"display notification {json.dumps(body)} with title {json.dumps(title)}"]
    else:
        cmd = ["notify-send", "-a", "MPV-UOS", "-i", icon, title, body]
    with contextlib.suppress(OSError, asyncio.TimeoutError):
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 10)


async def ask(title: str, body: str, actions: dict[str, str], timeout: float = 600.0,
              icon: str = ICON) -> str | None:
    """Show a notification with buttons and return the key of the one pressed (``None`` if none was).

    ``actions`` maps key → label. ``notify-send -A key=Label`` prints the key on stdout when it is pressed and
    nothing when the notification expires or is dismissed, so this coroutine can be awaited as the answer.
    """
    if not actions_available():
        await notify(title, body, icon)
        return None
    cmd = ["notify-send", "-a", "MPV-UOS", "-i", icon, "-w", "-t", str(int(timeout * 1000))]
    for key, label in actions.items():
        cmd += ["-A", f"{key}={label}"]
    cmd += [title, body]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout + 30)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    chosen = (out or b"").decode(errors="replace").strip()
    return chosen if chosen in actions else None
