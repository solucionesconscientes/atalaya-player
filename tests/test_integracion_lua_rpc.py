"""Every rpc.call() in the Lua scripts must hit a real mpvd method with parameter names it accepts (H34).

The Lua side and the daemon evolve separately, so a renamed method or parameter only shows up when the user opens that
menu. This reads the calls out of the scripts and checks them against the dispatcher the daemon really builds.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from mpvd.config import Settings
from mpvd.server import MpvdServer

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "mpv-config"
CALL_RE = re.compile(r"rpc\.call\s*\(")


def lua_files() -> list[Path]:
    return sorted([*SCRIPTS.glob("scripts/*.lua"), *SCRIPTS.glob("scripts/*/main.lua"),
                   *SCRIPTS.glob("script-modules/mu/*.lua")])


def _blank_comments(text: str) -> str:
    """The same text with Lua comments blanked out (offsets and line numbers kept): a ``-- (H31)`` inside a call
    would otherwise unbalance the bracket scan."""
    out = list(text)
    i, quote = 0, ""
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or (quote == "\n" and c == "\n"):
                quote = ""
        elif c in "'\"":
            quote = c
        elif text.startswith("--[[", i):
            end = text.find("]]", i)
            end = len(text) if end < 0 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        elif text.startswith("--", i):
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            for j in range(i, end):
                out[j] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _balanced(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> tuple[str, int]:
    """Substring from the bracket at ``start`` to its match, ignoring brackets inside quoted strings."""
    depth, i, quote = 0, start, ""
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
        i += 1
    raise AssertionError(f"unbalanced {open_ch} at offset {start}")


def _table_keys(table: str) -> set[str]:
    """Keys written at the top level of a Lua table literal: ``k = v`` and ``['k'] = v`` (nested tables ignored)."""
    keys: set[str] = set()
    depth, i, quote = 0, 0, ""
    while i < len(table):
        c = table[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "'\"":
            quote = c
        elif c in "{([":
            depth += 1
        elif c in "})]":
            depth -= 1
        elif depth == 1:
            m = re.match(r"\[\s*'([^']+)'\s*\]\s*=", table[i:])
            if m:
                keys.add(m.group(1))
                i += m.end()
                continue
            m = re.match(r"([A-Za-z_]\w*)\s*=(?!=)", table[i:])
            if m:
                keys.add(m.group(1))
                i += m.end()
                continue
        i += 1
    return keys


def _split_args(text: str, start: int) -> list[str]:
    """The top-level arguments of the call whose ``(`` is at ``start``."""
    call, _ = _balanced(text, start, "(", ")")
    inner = call[1:-1]
    args, depth, quote, cur = [], 0, "", []
    i = 0
    while i < len(inner):
        c = inner[i]
        if quote:
            if c == "\\":
                cur.append(inner[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c in "{([":
            depth += 1
        elif c in "})]":
            depth -= 1
        elif c == "," and depth == 0:
            args.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    args.append("".join(cur))
    return args


def _methods_in(expr: str) -> list[str]:
    """The method names an argument expression can produce.

    Literal (``'ping'``), the ``cond and 'a' or 'b'`` idiom Lua uses for two methods, and a concatenation
    (``'remote.' .. action``), which keeps its literal part plus a ``*``. An expression with no literal at all (a
    variable) yields nothing: there is nothing to check statically.
    """
    out = []
    for m in re.finditer(r"'([^']*)'", expr):
        name = m.group(1)
        if re.match(r"\s*\.\.", expr[m.end():]):
            name += "*"
        if "." in name or name.isidentifier():
            out.append(name)
    return out


def lua_calls() -> list[tuple[Path, int, str, set[str] | None]]:
    """(file, line, method, param names) for every rpc.call in the scripts; params is None when unknown."""
    calls = []
    for path in lua_files():
        text = _blank_comments(path.read_text(encoding="utf-8"))
        for m in CALL_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            args = _split_args(text, m.end() - 1)
            names = _methods_in(args[0])
            if not names:
                continue
            params: set[str] | None = None
            second = args[1].strip() if len(args) > 1 else ""
            if second.startswith("{"):
                params = _table_keys(_balanced(second, 0)[0])
            elif re.match(r"nil\b", second):
                params = set()
            for name in names:
                calls.append((path, line, name, params))
    return calls


@pytest.fixture(scope="module")
def daemon_methods(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """{name: (params, accepts_any_keyword)} of the dispatcher the real daemon builds (no socket, no dirs)."""
    tmp = tmp_path_factory.mktemp("caps")
    server = MpvdServer(Settings(runtime_dir=tmp / "rt", cache_dir=tmp / "cache", idle_timeout=0, workers=1))
    out = {}
    for info in server.dispatcher.methods():
        kwargs = any(p.kind is p.VAR_KEYWORD for p in inspect.signature(info.handler).parameters.values())
        out[info.name] = (set(info.params), kwargs)
    return out


def test_hay_llamadas_que_revisar():
    calls = lua_calls()
    assert len(calls) > 150, f"el extractor de rpc.call se ha quedado corto: {len(calls)}"
    assert len({c[2] for c in calls}) > 100


def test_todo_metodo_que_llama_lua_existe_en_mpvd(daemon_methods):
    missing = []
    for path, line, name, _ in lua_calls():
        if name.endswith("*"):
            prefix = name[:-1]
            if not any(k.startswith(prefix) for k in daemon_methods):
                missing.append(f"{path.relative_to(ROOT)}:{line} {prefix}…")
        elif name not in daemon_methods:
            missing.append(f"{path.relative_to(ROOT)}:{line} {name}")
    assert not missing, "métodos que el Lua llama y mpvd no registra:\n" + "\n".join(missing)


def test_los_parametros_que_pasa_lua_existen_en_el_metodo(daemon_methods):
    wrong = []
    for path, line, name, params in lua_calls():
        if params is None or name not in daemon_methods:
            continue
        accepted, any_keyword = daemon_methods[name]
        if any_keyword:
            continue
        unknown = sorted(params - accepted)
        if unknown:
            wrong.append(f"{path.relative_to(ROOT)}:{line} {name}: {unknown} (acepta {sorted(accepted)})")
    assert not wrong, "parámetros que mpvd no acepta:\n" + "\n".join(wrong)
