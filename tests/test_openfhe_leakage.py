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
