# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The leakage harness against a real local-mode collection: what the server sees of searches at
each level. Synthetic data only. Needs `ombra._native` (see `make test-openfhe-local`)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("openfhe")
pytest.importorskip("grpc_tools")
pytest.importorskip("faiss")
pytest.importorskip("ombra._native", reason="the compiled extension is not built")

import ombra  # noqa: E402
from ombra.search import run_search  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "leakage", REPO_ROOT / "core" / "bench" / "leakage.py"
)
lk = importlib.util.module_from_spec(_spec)
sys.modules["leakage"] = lk
_spec.loader.exec_module(lk)

pytestmark = pytest.mark.openfhe

D, N, CLUSTERS = 384, 120, 12


def _unit(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("leak")
    rng = np.random.default_rng(11)
    centers = _unit(rng.standard_normal((CLUSTERS, D)))
    x = _unit(centers[np.arange(N) % CLUSTERS] + 0.3 * rng.standard_normal((N, D)) / np.sqrt(D))
    ids = [f"synthetic-record-{i:08d}" for i in range(N)]
    client = ombra.Client.local(tmp / "data")
    client.create_collection(
        "leak",
        x,
        ids,
        key_file=tmp / "leak.key",
        passphrase="pw",  # noqa: S106
        scrypt_params=(2**4, 8, 1),
        n_clusters=CLUSTERS,
    )
    try:
        yield client, x, ids
    finally:
        client.close()


def _search(client, q, level, rerank):
    return lk.record(client, lambda: client.search("leak", q, k=3, level=level, rerank=rerank))


def test_strict_without_rerank_is_identical_across_queries_and_has_no_ids(world):
    client, x, ids = world
    a = _search(client, x[3], "strict", False)
    b = _search(client, x[77], "strict", False)
    assert a.n_calls > 0 and a.requested_clusters()
    assert a.shape(include_clusters=True) == b.shape(include_clusters=True)
    assert a.shape(include_clusters=True, include_ids=True) == b.shape(
        include_clusters=True, include_ids=True
    )
    assert a.collections() == {"leak"}
    assert not lk.contains_any(a, ids) and not lk.contains_any(b, ids)
    assert a.blob_ids()["GetBlob"] == []
    assert a.bytes_up > 0 and a.bytes_down > 0


def test_strict_with_rerank_shows_the_current_blob_id_leak(world):
    client, x, ids = world
    a = _search(client, x[3], "strict", True)
    b = _search(client, x[77], "strict", True)
    fetched = a.blob_ids()["GetBlob"]
    assert fetched and lk.contains_any(a, [i.encode() for i in ids])
    assert set(fetched) <= {i.encode() for i in ids}
    # sizes and counts alone do not tell the two apart; the fetched ids do
    assert a.shape(include_clusters=True) == b.shape(include_clusters=True)
    assert a.shape(include_clusters=True, include_ids=True) != b.shape(
        include_clusters=True, include_ids=True
    )


def test_fast_requests_different_clusters_for_distant_queries(world):
    client, x, _ = world
    far = [(i, j) for i in range(0, N, 13) for j in range(0, N, 17) if i % CLUSTERS != j % CLUSTERS]
    i, j = far[0]
    a = _search(client, x[i], "fast", False).requested_clusters()
    b = _search(client, x[j], "fast", False).requested_clusters()
    assert a and b and a != b


def test_decoy_rounds_are_padded_and_attack_runs(world):
    client, x, _ = world
    col, _ = client._core._open["leak"]
    views = []
    for _ in range(2):
        views.append(
            lk.record(
                client,
                lambda: run_search(col, x[5], 3, level="decoy", rerank=False),
                keep_payloads=False,
            )
        )
    assert len(views[0].requested_clusters()) == 2
    real = set(run_search(col, x[5], 3, level="decoy", rerank=False).probed)
    res = lk.intersection_attack(views, real)
    assert res["union"]["recall"] == 1.0  # the real clusters are in every search
    assert res["union"]["size"] <= col.cluster_count


# ---- search plus private fetch of the hits' documents ----------------------------------------

DOC_N, DOC_CLUSTERS, DOC_K = 40, 4, 10


def _doc(i):
    """Half the records get a small text (16 KiB class), half 100 KB of bytes (256 KiB class)."""
    if i % 2 == 0:
        return f"synthetic small document {i}"
    return np.random.default_rng(500 + i).integers(0, 256, 100_000, dtype=np.uint8).tobytes()


@pytest.fixture(scope="module")
def doc_world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("leakdoc")
    rng = np.random.default_rng(23)
    x = _unit(rng.standard_normal((DOC_N, D)))
    ids = [f"synthetic-doc-record-{i:08d}" for i in range(DOC_N)]
    client = ombra.Client.local(tmp / "data")
    client.create_collection(
        "leakdoc",
        x,
        ids,
        key_file=tmp / "leakdoc.key",
        passphrase="pw",  # noqa: S106
        preset="fast",
        scrypt_params=(2**4, 8, 1),
        n_clusters=DOC_CLUSTERS,
        private_retrieval=True,
        metadata=[{"title": f"title {i}"} for i in range(DOC_N)],
        documents=[_doc(i) for i in range(DOC_N)],
        metadata_bytes=128,
    )
    try:
        # the first call uploads the public keys of the private call; keep that out of the views
        client.search("leakdoc", x[0], k=DOC_K, level="strict", fetch_documents=True)
        yield client, x, ids
    finally:
        client.close()


def _doc_search(client, q):
    return lk.record(
        client,
        lambda: client.search("leakdoc", q, k=DOC_K, level="strict", fetch_documents=True),
    )


def test_strict_search_with_private_document_fetch_shows_no_hit_id(doc_world):
    client, x, ids = doc_world
    a = _doc_search(client, x[3])
    b = _doc_search(client, x[27])
    methods = [c.method for c in a.calls]
    assert "GetBlob" not in methods and a.blob_ids()["GetBlob"] == []
    assert methods.count("RetrieveDocuments") == 2  # one per practical class
    assert methods.count("RetrieveRecords") == 1
    for v in (a, b):
        assert not lk.contains_any(v, ids)
        for c in v.calls:
            if c.method == "RetrieveDocuments":
                assert c.requests[0].fields["start"]["all_clusters"] is True
    # two different queries: same calls, same counts, same sizes
    assert a.shape(include_clusters=True, include_ids=True) == b.shape(
        include_clusters=True, include_ids=True
    )
    assert a.bytes_up == b.bytes_up and a.bytes_down == b.bytes_down


def _row(name, calls):
    return (
        name,
        sum(c.bytes_up for c in calls),
        sum(c.bytes_down for c in calls),
        sum(len(c.requests) for c in calls),
        sum(len(c.responses) for c in calls),
    )


def test_bytes_and_messages_of_a_search_with_document_fetch(doc_world, capsys):
    """Measured sizes of one Strict search at k = 10 with the fetch of its hits' documents."""
    client, x, ids = doc_world
    v = _doc_search(client, x[5])
    docs = [c for c in v.calls if c.method == "RetrieveDocuments"]
    rows = [
        _row("search rounds", [c for c in v.calls if c.method == "Search"]),
        _row("records", [c for c in v.calls if c.method == "RetrieveRecords"]),
        _row("documents 16 KiB", docs[:1]),
        _row("documents 256 KiB", docs[1:]),
        _row("total", v.calls),
    ]
    for r in rows:
        assert r[1] > 0 and r[2] > 0 and r[3] > 1 and r[4] > 1
    with capsys.disabled():
        print("\nstep, bytes up, bytes down, messages up, messages down")
        for r in rows:
            print(*r, sep=", ")
