# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private fetch of metadata and documents end to end in local mode (CPU reference server).

Metadata comes back in the record row; `fetch_documents=True` returns the exact documents of the
final hits that are in the 16 KiB and 256 KiB classes, with one RetrieveDocuments call per class;
a larger document is reported as such. The Strict view holds no hit id and is the same for every
query. Synthetic data only; sizes are small (k = 2, so m = 2 and 3 buckets cannot drop a row).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.openfhe
ombra = pytest.importorskip("ombra")
pytest.importorskip("openfhe")
pytest.importorskip("ombra._native", reason="the compiled extension is not built")

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "leakage", REPO_ROOT / "core" / "bench" / "leakage.py"
)
lk = importlib.util.module_from_spec(_spec)
sys.modules["leakage"] = lk
_spec.loader.exec_module(lk)

N, D, CLUSTERS = 24, 384, 3
SCRYPT = (2**4, 8, 1)


def _unit(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def _doc(i: int):
    """None, a small text (16 KiB class), 100 KB of bytes (256 KiB), or 300 KB (2 MiB: large)."""
    rng = np.random.default_rng(1000 + i)
    kind = i % 4
    if kind == 0:
        return f"synthetic small document {i}"
    if kind == 1:
        return rng.integers(0, 256, 100_000, dtype=np.uint8).tobytes()
    if kind == 2:
        return None
    return rng.integers(0, 256, 300_000, dtype=np.uint8).tobytes()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pirdocs")
    rng = np.random.default_rng(31)
    x = _unit(rng.standard_normal((N, D)))
    ids = [f"synthetic-doc-record-{i:08d}" for i in range(N)]
    meta = [{"title": f"title {i}", "n": i, "tags": ["a", "b"]} for i in range(N)]
    docs = [_doc(i) for i in range(N)]
    client = ombra.Client.local(tmp / "data")
    client.create_collection(
        "docs",
        x,
        ids,
        key_file=tmp / "docs.key",
        passphrase="pw",  # noqa: S106
        preset="fast",
        n_clusters=CLUSTERS,
        scrypt_params=SCRYPT,
        private_retrieval=True,
        metadata=meta,
        documents=docs,
        metadata_bytes=256,
    )
    col = client._core._get("docs")[0]
    try:
        yield client, tmp, x, ids, meta, docs, col
    finally:
        client.close()


def _expect(hit, i, docs):
    d = docs[i]
    if d is None:
        assert hit.document is None and hit.document_status == "none"
    elif i % 4 == 3:
        assert hit.document is None and hit.document_status == "large"
    else:
        assert hit.document == d and hit.document_status == "fetched"


def test_metadata_comes_back_on_a_private_search(world):
    client, tmp, x, ids, meta, docs, col = world
    assert col.private_retrieval and col.metadata_bytes == 256 and col._v2
    hits = client.search("docs", x[5], k=2, level="strict")
    assert hits[0].id == ids[5] and hits[0].metadata == meta[5]
    assert all(h.document is None and h.document_status == "none" for h in hits)


def test_fetch_documents_returns_the_exact_documents_of_the_hits(world):
    client, tmp, x, ids, meta, docs, col = world
    seen = set()
    for i in (0, 1, 2, 3, 4, 5):
        hits = client.search("docs", x[i], k=2, level="strict", fetch_documents=True)
        assert hits[0].id == ids[i] and hits[0].metadata == meta[i]
        for h in hits:
            j = ids.index(h.id)
            _expect(h, j, docs)
            seen.add(h.document_status)
    assert {"fetched", "large", "none"} <= seen
    # the by-id path still reads a large document, and says it shows the id
    assert col.get_document(ids[3].encode()) == docs[3]
    assert "BY ID" in col.get_document.__doc__


def test_fast_level_scope_also_fetches(world):
    client, tmp, x, ids, meta, docs, col = world
    hits = client.search("docs", x[1], k=2, level="fast", fetch_documents=True)
    assert hits[0].id == ids[1] and hits[0].document == docs[1]


def test_fetch_documents_is_refused_without_v2_rows(world):
    client, tmp, x, ids, meta, docs, col = world
    with pytest.raises(ValueError, match="fetch_documents"):
        client.search("docs", x[0], k=2, rerank=False, fetch_documents=True)


def test_metadata_over_the_slot_is_refused_before_anything_is_sent(world):
    client, tmp, x, ids, meta, docs, col = world
    before = len(col.block_stats())
    big = [{"blob": "x" * 300}]
    with pytest.raises(ValueError, match="256"):
        client.insert("docs", x[:1], ["synthetic-too-big"], metadata=big)
    assert len(col.block_stats()) == before


def test_strict_view_of_a_document_search_has_no_id_and_is_the_same_for_every_query(world):
    client, tmp, x, ids, meta, docs, col = world
    views = [
        lk.record(
            client,
            lambda q=q: client.search("docs", q, k=2, level="strict", fetch_documents=True),
        )
        for q in (x[0], x[1], x[10], _unit(np.ones(D)))
    ]
    needles = [i.encode() for i in ids]
    for v in views:
        assert not lk.contains_any(v, needles)
        methods = [c.method for c in v.calls]
        assert "GetBlob" not in methods
        assert methods.count("RetrieveRecords") == 1
        assert methods.count("RetrieveDocuments") == 2  # one per practical class
        for c in v.calls:
            if c.method == "RetrieveDocuments":
                start = c.requests[0].fields["start"]
                assert start["all_clusters"] is True and start["m"] == 2
    shapes = [v.shape(include_clusters=True, include_ids=True) for v in views]
    assert shapes[0] == shapes[1] == shapes[2] == shapes[3]  # same calls, counts and sizes
    for v in views[1:]:
        assert v.bytes_up == views[0].bytes_up and v.bytes_down == views[0].bytes_down


def test_repack_rewrites_the_document_columns(world):
    client, tmp, x, ids, meta, docs, col = world
    gone = [ids[i] for i in range(0, 8)]
    client.delete("docs", gone)
    assert col.repack(threshold=0.1).groups >= 1
    alive = 15
    hits = client.search("docs", x[alive], k=2, level="strict", fetch_documents=True)
    assert hits[0].id == ids[alive]
    for h in hits:
        j = ids.index(h.id)
        assert j >= 8
        _expect(h, j, docs)
