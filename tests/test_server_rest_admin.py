# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The REST admin API of collections (/v1/collections) of the CPU-only `ombra_server`.

Needs only the server binary (`make build-cpu`) and the standard library: collections are made over
REST itself, with metadata only. One server with API keys per test module. Synthetic data only.
"""

from __future__ import annotations

import http.client
import json
import socket

import pytest
from server_harness import ServerProcess, find_server_binary

if find_server_binary() is None:
    pytest.skip("ombra_server (CPU build) not found: run `make build-cpu`", allow_module_level=True)

SPEC = {
    "preset": "m0_depth2",
    "dimension": 16,
    "security_level": "fast",
    "key_tag": "tag-1",
    "cluster_count": 2,
    "filter_fields": [{"field_id": 0, "cardinality": 3}],
}


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    root = tmp_path_factory.mktemp("rest")
    server = ServerProcess(
        root, auth=True, rest=True, openfhe_version="1.6.1", extra_args=["--log-format", "json"]
    )
    keys = {
        "admin": server.create_key("admin"),
        "write": server.create_key("write"),
        "read": server.create_key("read"),
        "read_a": server.create_key("read", "alpha"),
        "write_a": server.create_key("write", "alpha"),
    }
    server.start()
    server.keys = keys
    yield server
    server.kill()


def call(srv, method, path, key=None, body=None, headers=None):
    """(status, headers, parsed JSON or None)."""
    host, port = srv.rest_address.rsplit(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    h = dict(headers or {})
    if key:
        h["Authorization"] = f"Bearer {srv.keys.get(key, key)}"
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    out = (r.status, dict(r.getheaders()), json.loads(raw) if raw else None)
    conn.close()
    return out


def code(res):
    return res[2]["error"]["code"]


def test_create_get_list_delete(srv):
    st, hd, body = call(srv, "POST", "/v1/collections", "write", {"name": "alpha", **SPEC})
    assert st == 201, body
    assert body["name"] == "alpha" and body["preset"] == "m0_depth2"
    assert body["security_level"] == "fast" and body["vector_count"] == 0
    assert body["keys_uploaded"] is False
    assert len(hd["X-Request-Id"]) == 16

    st, _, body = call(srv, "GET", "/v1/collections/alpha", "read")
    assert st == 200
    assert body["dimension"] == 16 and body["cluster_count"] == 2
    assert body["filter_fields"] == [{"field_id": 0, "cardinality": 3}]
    assert body["live_count"] == 0 and body["deleted_count"] == 0
    assert body["disk_bytes"] >= 0 and body["blob_count"] == 0 and body["blob_bytes"] == 0
    assert "key_tag" in body

    call(srv, "POST", "/v1/collections", "write", {"name": "beta", **SPEC})
    st, _, body = call(srv, "GET", "/v1/collections", "read")
    assert st == 200
    assert sorted(c["name"] for c in body["collections"]) == ["alpha", "beta"]

    st, _, body = call(srv, "DELETE", "/v1/collections/beta", "admin")
    assert st == 200 and body == {"dropped": True}
    assert code(call(srv, "GET", "/v1/collections/beta", "read")) == "COLLECTION_NOT_FOUND"
    assert call(srv, "GET", "/v1/collections/beta", "read")[0] == 404
    assert call(srv, "DELETE", "/v1/collections/beta", "admin")[0] == 404
    # The collection is also visible over gRPC: it is the same catalog.
    st, _, body = call(srv, "GET", "/v1/collections", "read")
    assert [c["name"] for c in body["collections"]] == ["alpha"]


def test_create_errors(srv):
    call(srv, "POST", "/v1/collections", "write", {"name": "gamma", **SPEC})
    res = call(srv, "POST", "/v1/collections", "write", {"name": "gamma", **SPEC})
    assert res[0] == 409 and code(res) == "COLLECTION_EXISTS"
    res = call(srv, "POST", "/v1/collections", "write", {"name": "x", **SPEC, "preset": "nope"})
    assert res[0] == 400 and code(res) == "INVALID_ARGUMENT"
    res = call(srv, "POST", "/v1/collections", "write", {"name": "x", **SPEC, "cluster_count": 0})
    assert res[0] == 400 and code(res) == "INVALID_ARGUMENT"
    res = call(srv, "POST", "/v1/collections", "write", {"name": "x", **SPEC, "dimension": 10**6})
    assert res[0] == 400 and code(res) == "INVALID_FILTER_LAYOUT"
    res = call(srv, "POST", "/v1/collections", "write", {"name": "", **SPEC})
    assert res[0] == 400
    call(srv, "DELETE", "/v1/collections/gamma", "admin")


def test_bad_bodies(srv):
    for body in (b"not json", b"[]", b"{}", b'{"name":"x"', b""):
        res = call(srv, "POST", "/v1/collections", "write", body)
        assert res[0] == 400 and code(res) == "INVALID_ARGUMENT", body
    res = call(srv, "POST", "/v1/collections", "write", {"name": "x", **SPEC, "extra": 1})
    assert res[0] == 400
    bad = {**SPEC, "dimension": "16"}
    assert call(srv, "POST", "/v1/collections", "write", {"name": "x", **bad})[0] == 400
    # No security level: the caller must choose.
    no_level = {k: v for k, v in SPEC.items() if k != "security_level"}
    assert call(srv, "POST", "/v1/collections", "write", {"name": "x", **no_level})[0] == 400


def test_oversized_body(srv):
    big = b'{"name":"' + b"a" * (64 * 1024) + b'"}'
    res = call(srv, "POST", "/v1/collections", "write", big)
    assert res[0] == 413 and code(res) == "PAYLOAD_TOO_LARGE"
    # A huge Content-Length is refused before any body is read or any key is checked.
    host, port = srv.rest_address.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=10) as s:
        s.sendall(b"POST /v1/collections HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n")
        assert s.recv(4096).startswith(b"HTTP/1.1 413")
    # Exactly at the cap is read (and then refused as bad JSON, not as too large).
    res = call(srv, "POST", "/v1/collections", "write", b" " * (64 * 1024))
    assert res[0] == 400


def test_auth(srv):
    res = call(srv, "GET", "/v1/collections")
    assert res[0] == 401 and code(res) == "UNAUTHENTICATED"
    assert res[2]["error"]["request_id"] == res[1]["X-Request-Id"]
    assert call(srv, "GET", "/v1/collections", "0" * 64)[0] == 401
    assert call(srv, "GET", "/v1/collections", headers={"Authorization": "Basic abc"})[0] == 401
    # Unauthenticated callers learn nothing about a collection.
    assert call(srv, "GET", "/v1/collections/alpha")[0] == 401
    assert call(srv, "GET", "/v1/collections/nothing")[0] == 401
    assert call(srv, "DELETE", "/v1/collections/alpha")[0] == 401
    assert call(srv, "POST", "/v1/collections", body=dict(SPEC, name="z"))[0] == 401


def test_scope_refusals(srv):
    call(srv, "POST", "/v1/collections", "write", {"name": "delme", **SPEC})
    for key in ("read", "write", "read_a", "write_a"):
        res = call(srv, "DELETE", "/v1/collections/delme", key)
        assert res[0] == 403 and code(res) == "PERMISSION_DENIED", key
    assert call(srv, "GET", "/v1/collections/delme", "read")[0] == 200  # still there
    # A read key cannot create.
    res = call(srv, "POST", "/v1/collections", "read", {"name": "r", **SPEC})
    assert res[0] == 403
    assert call(srv, "GET", "/v1/collections/r", "read")[0] == 404
    assert call(srv, "DELETE", "/v1/collections/delme", "admin")[0] == 200


def test_collection_scoped_keys(srv):
    call(srv, "POST", "/v1/collections", "write", {"name": "bravo", **SPEC})
    # Scoped to alpha: sees alpha only, in the list and by name.
    res = call(srv, "GET", "/v1/collections", "read_a")
    assert res[0] == 200 and [c["name"] for c in res[2]["collections"]] == ["alpha"]
    assert call(srv, "GET", "/v1/collections/alpha", "read_a")[0] == 200
    refused = call(srv, "GET", "/v1/collections/bravo", "read_a")
    missing = call(srv, "GET", "/v1/collections/does-not-exist", "read_a")
    assert refused[0] == missing[0] == 403  # the same answer, existing or not
    assert refused[2]["error"]["message"] == missing[2]["error"]["message"]
    # Create: only its own name.
    assert call(srv, "POST", "/v1/collections", "write_a", {"name": "bravo2", **SPEC})[0] == 403
    assert call(srv, "GET", "/v1/collections/bravo2", "admin")[0] == 404
    res = call(srv, "POST", "/v1/collections", "write_a", {"name": "alpha", **SPEC})
    assert res[0] == 409  # alpha exists; it was allowed to try
    # An unscoped read key sees both.
    res = call(srv, "GET", "/v1/collections", "read")
    assert {c["name"] for c in res[2]["collections"]} >= {"alpha", "bravo"}
    call(srv, "DELETE", "/v1/collections/bravo", "admin")


def test_routes_and_methods(srv):
    res = call(srv, "GET", "/v1/nothing", "admin")
    assert res[0] == 404 and code(res) == "NOT_FOUND"
    assert call(srv, "GET", "/v1/collections/a/b", "admin")[0] == 404
    assert call(srv, "GET", "/v1/collections/a%2Fb", "admin")[0] == 404
    assert call(srv, "GET", "/v1/", "admin")[0] == 404
    res = call(srv, "PUT", "/v1/collections", "admin", SPEC)
    assert res[0] == 405 and code(res) == "METHOD_NOT_ALLOWED"
    assert res[1]["Allow"] == "GET, POST"
    res = call(srv, "POST", "/v1/collections/alpha", "admin", {})
    assert res[0] == 405 and res[1]["Allow"] == "GET, DELETE"
    assert call(srv, "PATCH", "/v1/collections/alpha", "admin")[0] == 405
    # The old routes are unchanged.
    assert call(srv, "GET", "/healthz")[0] == 200
    assert call(srv, "GET", "/metrics", "read")[0] == 403
    assert call(srv, "GET", "/metrics", "admin")[0] == 200
    assert call(srv, "GET", "/nothing")[0] == 404
    assert call(srv, "POST", "/healthz")[0] == 405


def test_request_id_and_logs(srv):
    st, hd, _ = call(srv, "GET", "/v1/collections", "read", headers={"X-Request-Id": "my-req.1"})
    assert st == 200 and hd["X-Request-Id"] == "my-req.1"
    st, hd, _ = call(srv, "GET", "/v1/collections", "read", headers={"X-Request-Id": "bad id!"})
    assert hd["X-Request-Id"] != "bad id!" and len(hd["X-Request-Id"]) == 16
    call(srv, "POST", "/v1/collections", "write", {"name": "secretname", **SPEC})
    call(srv, "GET", "/v1/collections/secretname", "read", headers={"X-Request-Id": "log-me"})
    call(srv, "DELETE", "/v1/collections/secretname", "admin")
    text = srv.log_path.read_text()
    lines = [json.loads(x) for x in text.splitlines() if x.startswith("{")]
    mine = [x for x in lines if x.get("request_id") == "log-me"]
    assert mine and mine[0]["route"] == "get_collection" and mine[0]["status"] == 200
    assert mine[0]["key_id"] and all(k not in text for k in srv.keys.values())
    assert "secretname" not in text  # names never reach the log


def test_no_auth_server_refuses_nothing_but_stays_loopback(tmp_path):
    # --no-auth: everything is allowed on loopback, as for gRPC.
    s = ServerProcess(tmp_path, rest=True, openfhe_version="1.6.1")
    with s:
        s.keys = {}
        assert call(s, "POST", "/v1/collections", body={"name": "n", **SPEC})[0] == 201
        assert call(s, "DELETE", "/v1/collections/n")[0] == 200
