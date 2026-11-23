# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Fixed decoy groups on a real local-mode collection (CPU, synthetic data, d = 384): the grouping
is stored in the sealed stats and survives reopen and re-clustering, Decoy requests are whole
groups with the fixed per-round counts, and the default stays off."""

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
from ombra.probe import decoy_round_sizes  # noqa: E402
from ombra.search import run_search  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "leakage", REPO_ROOT / "core" / "bench" / "leakage.py"
)
lk = importlib.util.module_from_spec(_spec)
sys.modules["leakage"] = lk
_spec.loader.exec_module(lk)

pytestmark = pytest.mark.openfhe

D, N, CLUSTERS = 384, 160, 16


def _unit(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def _make(tmp, **kw):
    rng = np.random.default_rng(5)
    centers = _unit(rng.standard_normal((CLUSTERS, D)))
    x = _unit(centers[np.arange(N) % CLUSTERS] + 0.3 * rng.standard_normal((N, D)) / np.sqrt(D))
    ids = [f"synthetic-record-{i:08d}" for i in range(N)]
    client = ombra.Client.local(tmp / "data")
    client.create_collection(
        "g",
        x,
        ids,
        key_file=tmp / "g.key",
        passphrase="pw",  # noqa: S106
        scrypt_params=(2**4, 8, 1),
        n_clusters=CLUSTERS,
        **kw,
    )
    col, _ = client._core._open["g"]
    return client, col, x


def test_default_collection_has_no_groups(tmp_path):
    client, col, _ = _make(tmp_path)
    try:
        assert col.stats.groups is None
    finally:
        client.close()


@pytest.mark.parametrize("method", ["random", "neighbours"])
def test_grouped_collection_requests_whole_groups_and_finds_the_neighbours(tmp_path, method):
    g = 2
    client, col, x = _make(tmp_path, decoy_group_size=g, decoy_grouping=method)
    try:
        info = col.stats.groups
        assert info.size == g and info.method == method
        assert col.cluster_count == CLUSTERS
        views = []
        for i in (3, 40, 99):
            views.append(
                lk.record(client, lambda i=i: client.search("g", x[i], k=3, level="decoy"))
            )
        want = decoy_round_sizes(4, CLUSTERS, g)
        for i, v in zip((3, 40, 99), views, strict=True):
            rounds = v.requested_clusters()
            assert tuple(len(r) for r in rounds) == want
            flat = [c for r in rounds for c in r]
            assert len(flat) == len(set(flat))
            for r in rounds:
                for c in r:
                    assert set(np.nonzero(info.group_of == info.group_of[c])[0]) <= set(r)
            assert client.search("g", x[i], k=1, level="decoy")[0].score > 0.99  # finds itself
        a = lk.record(client, lambda: client.search("g", x[3], k=3, level="decoy"))
        b = lk.record(client, lambda: client.search("g", x[3], k=3, level="decoy"))
        assert a.requested_clusters() == b.requested_clusters()  # same real groups, same bytes
        # reopen: the sealed stats come back with the grouping
        again = ombra.Client.local(tmp_path / "data")
        try:
            again.open_collection("g", key_file=tmp_path / "g.key", passphrase="pw")  # noqa: S106
            col2, _ = again._core._open["g"]
            assert np.array_equal(col2.stats.groups.group_of, info.group_of)
        finally:
            again.close()
    finally:
        client.close()


def test_recluster_keeps_groups_with_a_new_keyed_grouping(tmp_path):
    client, col, x = _make(tmp_path, decoy_group_size=4, decoy_grouping="random")
    try:
        new = col.recluster(CLUSTERS, seed=2)
        try:
            assert new.stats.groups.size == 4 and new.stats.groups.method == "random"
            assert new.stats.groups.salt != col.stats.groups.salt
            hits = run_search(new, x[7], 1, level="decoy")
            assert hits.rounds == 2 and hits.hits[0].score > 0.99
        finally:
            new.retire()
    finally:
        client.close()


def test_group_size_is_checked(tmp_path):
    with pytest.raises(ValueError):
        _make(tmp_path, decoy_group_size=0)
    with pytest.raises(ValueError):
        _make(tmp_path / "b", decoy_group_size=2, decoy_grouping="bogus")
