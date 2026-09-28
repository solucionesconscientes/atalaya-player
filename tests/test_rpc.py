"""JSON-RPC 2.0 dispatcher: requests, notifications, batches and error codes."""

import asyncio
import json

import pytest

from mpvd import rpc


def make() -> rpc.Dispatcher:
    d = rpc.Dispatcher()

    @d.method("add")
    async def add(ctx, a, b):
        """Add two numbers."""
        return a + b

    @d.method("echo")
    async def echo(ctx, **kw):
        return kw

    @d.method("boom")
    async def boom(ctx):
        raise ValueError("kaboom")

    @d.method("notfound")
    async def notfound(ctx):
        raise rpc.RpcError(rpc.NOT_FOUND, "nothing here", {"x": 1})

    return d


def run(d: rpc.Dispatcher, raw):
    return asyncio.run(d.handle_text(raw if isinstance(raw, str) else json.dumps(raw), ctx=None))


def test_by_name_and_by_position():
    d = make()
    assert json.loads(run(d, {"jsonrpc": "2.0", "id": 1, "method": "add", "params": {"a": 1, "b": 2}})) == {
        "jsonrpc": "2.0", "id": 1, "result": 3}
    assert json.loads(run(d, {"jsonrpc": "2.0", "id": "x", "method": "add", "params": [2, 3]}))["result"] == 5
    assert json.loads(run(d, {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"k": "v"}}))["result"] == {"k": "v"}


def test_notification_has_no_response():
    d = make()
    assert run(d, {"jsonrpc": "2.0", "method": "add", "params": [1, 1]}) is None
    assert run(d, {"jsonrpc": "2.0", "method": "boom"}) is None


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("{not json", rpc.PARSE_ERROR),
        ({"jsonrpc": "1.0", "id": 1, "method": "add"}, rpc.INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1}, rpc.INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1, "method": "nope"}, rpc.METHOD_NOT_FOUND),
        ({"jsonrpc": "2.0", "id": 1, "method": "add", "params": {"a": 1}}, rpc.INVALID_PARAMS),
        ({"jsonrpc": "2.0", "id": 1, "method": "add", "params": [1, 2, 3]}, rpc.INVALID_PARAMS),
        ({"jsonrpc": "2.0", "id": 1, "method": "add", "params": 5}, rpc.INVALID_PARAMS),
        ({"jsonrpc": "2.0", "id": 1, "method": "boom"}, rpc.INTERNAL_ERROR),
        ({"jsonrpc": "2.0", "id": 1, "method": "notfound"}, rpc.NOT_FOUND),
        ([], rpc.INVALID_REQUEST),
    ],
)
def test_error_codes(raw, code):
    resp = json.loads(run(make(), raw))
    assert resp["error"]["code"] == code
    assert "result" not in resp


def test_application_error_carries_data():
    resp = json.loads(run(make(), {"jsonrpc": "2.0", "id": 9, "method": "notfound"}))
    assert resp["error"] == {"code": rpc.NOT_FOUND, "message": "nothing here", "data": {"x": 1}}


def test_batch_mixes_results_errors_and_notifications():
    d = make()
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "add", "params": [1, 2]},
        {"jsonrpc": "2.0", "method": "add", "params": [0, 0]},
        {"jsonrpc": "2.0", "id": 2, "method": "nope"},
        "garbage",
    ]
    out = json.loads(run(d, batch))
    assert [o.get("id") for o in out] == [1, 2, None]
    assert out[0]["result"] == 3
    assert out[1]["error"]["code"] == rpc.METHOD_NOT_FOUND
    assert out[2]["error"]["code"] == rpc.INVALID_REQUEST
    assert run(d, [{"jsonrpc": "2.0", "method": "add", "params": [1, 1]}]) is None


def test_methods_listing_includes_params_and_doc():
    info = {m.name: m for m in make().methods()}
    assert info["add"].params == ["a", "b"]
    assert info["add"].doc == "Add two numbers."
    assert list(info) == sorted(info)
