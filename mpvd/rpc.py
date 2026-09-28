"""JSON-RPC 2.0 message handling (spec: https://www.jsonrpc.org/specification).

Transport-agnostic: ``Dispatcher.handle_text`` takes one raw message (a request, a notification or a
batch) and returns the encoded response text, or ``None`` when nothing must be sent (notifications).
Handlers are ``async def fn(ctx, **params)`` (by-name params) or ``async def fn(ctx, *params)``
(by-position); ``ctx`` is whatever the transport passes (connection, session...).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("mpvd.rpc")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Application errors (-32000..-32099 reserved by the spec for server errors).
NOT_FOUND = -32001
UNAVAILABLE = -32002
CANCELLED = -32003


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_obj(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


Handler = Callable[..., Awaitable[Any]]


@dataclass
class MethodInfo:
    name: str
    handler: Handler
    doc: str = ""
    params: list[str] = field(default_factory=list)


def _error_response(rid: Any, err: RpcError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": err.to_obj()}


class Dispatcher:
    def __init__(self) -> None:
        self._methods: dict[str, MethodInfo] = {}

    # -- registration --------------------------------------------------------

    def register(self, name: str, handler: Handler, doc: str | None = None) -> None:
        sig = inspect.signature(handler)
        params = [p.name for p in list(sig.parameters.values())[1:] if p.kind is not p.VAR_KEYWORD]
        self._methods[name] = MethodInfo(name, handler, (doc or inspect.getdoc(handler) or "").strip(), params)

    def method(self, name: str, doc: str | None = None) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.register(name, fn, doc)
            return fn

        return deco

    def methods(self) -> list[MethodInfo]:
        return [self._methods[k] for k in sorted(self._methods)]

    # -- dispatch --------------------------------------------------------------

    async def call(self, name: str, params: Any, ctx: Any) -> Any:
        info = self._methods.get(name)
        if info is None:
            raise RpcError(METHOD_NOT_FOUND, f"method not found: {name}")
        try:
            if params is None:
                return await info.handler(ctx)
            if isinstance(params, dict):
                return await info.handler(ctx, **params)
            if isinstance(params, list):
                return await info.handler(ctx, *params)
        except TypeError as exc:
            # Wrong arity / unknown keyword: report as invalid params, but not TypeErrors raised deep inside.
            tb = exc.__traceback__
            depth = 0
            while tb is not None:
                depth += 1
                tb = tb.tb_next
            if depth <= 2:
                raise RpcError(INVALID_PARAMS, str(exc)) from exc
            raise
        raise RpcError(INVALID_PARAMS, "params must be an object or an array")

    async def _handle_one(self, msg: Any, ctx: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error_response(msg.get("id") if isinstance(msg, dict) else None,
                                   RpcError(INVALID_REQUEST, "invalid request"))
        rid = msg.get("id")
        is_notification = "id" not in msg
        try:
            result = await self.call(msg["method"], msg.get("params"), ctx)
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except RpcError as exc:
            return None if is_notification else _error_response(rid, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any handler failure becomes a JSON-RPC error
            log.exception("handler %s failed", msg["method"])
            if is_notification:
                return None
            return _error_response(rid, RpcError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))

    async def handle_obj(self, obj: Any, ctx: Any) -> Any | None:
        if isinstance(obj, list):
            if not obj:
                return _error_response(None, RpcError(INVALID_REQUEST, "empty batch"))
            results = await asyncio.gather(*(self._handle_one(m, ctx) for m in obj))
            out = [r for r in results if r is not None]
            return out or None
        return await self._handle_one(obj, ctx)

    async def handle_text(self, raw: str | bytes, ctx: Any) -> str | None:
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            return encode(_error_response(None, RpcError(PARSE_ERROR, f"parse error: {exc}")))
        resp = await self.handle_obj(obj, ctx)
        return None if resp is None else encode(resp)


def encode(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def make_request(method: str, params: Any = None, rid: Any = 1) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": rid}
    if params is not None:
        msg["params"] = params
    return msg


def make_notification(method: str, params: Any = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg
