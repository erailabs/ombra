# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private record retrieval end to end in local mode: the private re-rank returns what the blob
re-rank returns, the server's view holds no record id, and Strict searches look alike.

Local mode answers RetrieveRecords with the CPU reference (`ombra.pir_reference`). Synthetic data
only; sizes are small (a few rows, k = 2) so that the cuckoo placement of m = 6 rows has room.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.openfhe
ombra = pytest.importorskip("ombra")
pytest.importorskip("openfhe")
pytest.importorskip("ombra._native", reason="the compiled extension is not built")

from ombra.search import run_search  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "leakage", REPO_ROOT / "core" / "bench" / "leakage.py"
)
lk = importlib.util.module_from_spec(_spec)
sys.modules["leakage"] = lk
_spec.loader.exec_module(lk)

N, D, CLUSTERS = 36, 384, 3
SCRYPT = (2**4, 8, 1)


def _seeded(seed):
    return random.Random(seed)  # noqa: S311  # reproducible test plans


def _unit(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def _data(n=N, seed=21):
    rng = np.random.default_rng(seed)
    return _unit(rng.standard_normal((n, D)))


def _make(client, tmp, name, x, ids, private, **extra):
    client.create_collection(
        name,
        x,
        ids,
        key_file=tmp / f"{name}.key",
        passphrase="pw",  # noqa: S106
        preset="fast",
        n_clusters=CLUSTERS,
        scrypt_params=SCRYPT,
        private_retrieval=private,
        **extra,
    )
    return client._core._get(name)[0]


def _first_clean(col, q, k, **opts):
    """Search with fixed seeds until a call has no cuckoo drop: a drop is legal (counted, never
    retried) but would make the comparison with the blob re-rank depend on the hash seed."""
    for seed in range(12):
        res = run_search(col, q, k, _rng=_seeded(seed), **opts)
        if res.pir_dropped == 0 and res.pir_stale == 0:
            return res
    raise AssertionError("every seed dropped a candidate")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pirlocal")
    x = _data()
    ids = [f"synthetic-record-{i:08d}" for i in range(N)]
    client = ombra.Client.local(tmp / "data")
    priv = _make(client, tmp, "priv", x, ids, True)
    plain = _make(client, tmp, "plain", x, ids, False)
    try:
        yield client, tmp, x, ids, priv, plain
    finally:
        client.close()


def test_private_rerank_matches_blob_rerank(world):
    client, tmp, x, ids, priv, plain = world
    assert priv.private_retrieval and not plain.private_retrieval
    rng = np.random.default_rng(3)
    for level in ("strict", "fast", "decoy"):
        for _ in range(2):
            q = _unit(x[int(rng.integers(0, N))] + 0.2 * rng.standard_normal(D) / np.sqrt(D))
            a = _first_clean(priv, q, 2, level=level)
            b = run_search(plain, q, 2, level=level, _rng=_seeded(0))
            assert a.private and not b.private
            assert [h.id for h in a.hits] == [h.id for h in b.hits]
            assert [h.score for h in a.hits] == [h.score for h in b.hits]  # same float32 rows
            assert all(h.metadata == {} for h in a.hits)
            assert a.fetched == 6 and a.pir_bad_rows == 0


def test_hits_have_empty_metadata_and_get_by_id_still_works(world):
    client, tmp, x, ids, priv, plain = world
    hits = client.search("priv", x[5], k=2, level="strict")
    assert hits and hits[0].id == ids[5] and hits[0].metadata == {}
    vec, meta = priv.get_record(ids[5].encode())
    assert vec is not None and meta == {}


def test_server_view_of_a_private_search_has_no_record_id(world):
    client, tmp, x, ids, priv, plain = world
    a = lk.record(client, lambda: client.search("priv", x[3], k=2, level="strict"))
    b = lk.record(client, lambda: client.search("priv", x[30], k=2, level="strict"))
    methods = {c.method for c in a.calls}
    assert "RetrieveRecords" in methods and "GetBlob" not in methods
    assert a.blob_ids()["GetBlob"] == []
    needles = [i.encode() for i in ids]
    assert not lk.contains_any(a, needles) and not lk.contains_any(b, needles)
    # the blob re-rank of the same search does show them
    c = lk.record(client, lambda: client.search("plain", x[3], k=2, level="strict"))
    assert lk.contains_any(c, needles)


def test_strict_views_of_two_queries_are_identical(world):
    client, tmp, x, ids, priv, plain = world
    views = [
        lk.record(client, lambda q=q: client.search("priv", q, k=2, level="strict"))
        for q in (x[3], x[30], _unit(np.ones(D)))
    ]
    shapes = [v.shape(include_clusters=True, include_ids=True) for v in views]
    assert shapes[0] == shapes[1] == shapes[2]
    for v in views:
        calls = [c for c in v.calls if c.method == "RetrieveRecords"]
        assert len(calls) == 1
        start = calls[0].requests[0].fields["start"]
        assert start["all_clusters"] is True and start["m"] == 6


def test_fast_scope_is_the_requested_clusters(world):
    client, tmp, x, ids, priv, plain = world
    v = lk.record(client, lambda: client.search("priv", x[3], k=2, level="fast"))
    asked = set().union(*v.requested_clusters())
    call = next(c for c in v.calls if c.method == "RetrieveRecords")
    start = call.requests[0].fields["start"]
    assert set(start["cluster_ids"]) == asked and "all_clusters" not in start


def test_insert_after_create_and_reopen_without_upload(world):
    client, tmp, x, ids, priv, plain = world
    extra = _data(3, seed=77)
    new_ids = [f"synthetic-extra-{i:08d}" for i in range(3)]
    client.insert("priv", extra, new_ids)
    hits = client.search("priv", extra[1], k=1, level="strict")
    assert [h.id for h in hits] == [new_ids[1]]
    # a new process: opens with the key file, finds the keys on the server, uploads nothing
    c2 = ombra.Client.local(tmp / "data")
    try:
        c2.open_collection("priv", key_file=tmp / "priv.key", passphrase="pw")  # noqa: S106
        col2 = c2._core._get("priv")[0]
        assert col2.private_retrieval
        uploads = []
        real = col2.stub.UploadKeys
        col2.stub.UploadKeys = lambda it: uploads.append(1) or real(it)
        assert [h.id for h in c2.search("priv", extra[2], k=1, level="strict")] == [new_ids[2]]
        assert uploads == []
    finally:
        c2.close()


def test_server_with_other_retrieval_keys_gets_one_new_upload(world):
    from ombra.pir import PirClient

    client, tmp, x, ids, priv, plain = world
    uploads = []
    real = priv.stub.UploadKeys
    old_pir = priv.kit.pir
    priv.stub.UploadKeys = lambda it: uploads.append(1) or real(it)
    priv.kit.pir = PirClient()  # another key pair: the stored keys carry another tag
    try:
        hits = client.search("priv", x[7], k=1, level="strict")
        assert hits[0].id == ids[7] and uploads == [1]
        client.search("priv", x[8], k=1, level="strict")
        assert uploads == [1]  # now the server has them
    finally:
        priv.kit.pir = old_pir
        priv.stub.UploadKeys = real
        client.search("priv", x[7], k=1, level="strict")  # the original keys, uploaded again


def test_repack_rewrites_the_record_column(world):
    client, tmp, x, ids, priv, plain = world
    gone = [ids[i] for i in range(0, 12)]
    for name in ("priv", "plain"):
        client.delete(name, gone)
    report = priv.repack(threshold=0.1)
    assert report.groups >= 1
    q = _unit(x[20] + 0.1 * np.random.default_rng(9).standard_normal(D) / np.sqrt(D))
    a = _first_clean(priv, q, 2, level="strict")
    b = run_search(plain, q, 2, level="strict", _rng=_seeded(0))
    assert [h.id for h in a.hits] == [h.id for h in b.hits]
    assert not {h.id.decode() for h in a.hits} & set(gone)
    assert a.pir_bad_rows == 0


def test_a_row_that_fails_its_tag_is_dropped_and_counted(world):
    """A stale or tampered row never reaches the scores."""
    from ombra import private_retrieval as pr

    client, tmp, x, ids, priv, plain = world
    real = pr.ROW_VERSION
    pr.ROW_VERSION = real + 1  # the client now expects another row version than the stored one
    try:
        res = run_search(priv, x[25], 2, level="strict", _rng=_seeded(1))
    finally:
        pr.ROW_VERSION = real
    assert res.hits == [] and res.pir_bad_rows > 0


def test_private_retrieval_with_range_fields(tmp_path):
    n = 30
    x = _data(n, seed=5)
    rng = np.random.default_rng(6)
    meta = [
        {"year": int(y), "price": int(p), "lang": int(g)}
        for y, p, g in zip(
            rng.integers(1990, 2030, n),
            rng.integers(0, 4096, n),
            rng.integers(0, 3, n),
            strict=True,
        )
    ]
    ids = [f"synthetic-range-{i:08d}" for i in range(n)]
    client = ombra.Client.local(tmp_path / "data")
    try:
        opts = {
            "metadata": meta,
            "filter_fields": {"lang": 3},
            "range_fields": {"year": [2000, 2010, 2020], "price": {"kind": "two_digit"}},
        }
        priv = _make(client, tmp_path, "rp", x, ids, True, **opts)
        plain = _make(client, tmp_path, "rn", x, ids, False, **opts)
        q = _unit(x[4] + 0.1 * rng.standard_normal(D) / np.sqrt(D))
        for flt in (
            {"year": {"gte": 2005, "lt": 2018}, "lang": [0, 1]},
            {"price": {"gte": 300, "lte": 2500}},
        ):
            names = client._core._get("rp")[1]
            ranges = {f: w for f, w in flt.items() if priv.schema.get(f) is not None}
            eq = {names.index(f): w for f, w in flt.items() if f in names}
            kw = {"filter": eq or None, "range_plan": priv.schema.plan(ranges)}
            a = _first_clean(priv, q, 1, level="strict", **kw)
            b = run_search(plain, q, 1, level="strict", _rng=_seeded(0), **kw)
            assert [h.id for h in a.hits] == [h.id for h in b.hits], flt
            assert a.fetched == 4 * 3 and a.private
    finally:
        client.close()
