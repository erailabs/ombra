# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Range filters end to end in local mode (everything on the client). Synthetic data only."""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.openfhe
ombra = pytest.importorskip("ombra")
pytest.importorskip("openfhe")

N, D = 120, 384  # d = 384 is the smallest supported dimension


def _data():
    rng = np.random.default_rng(5)
    x = rng.standard_normal((N, D))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    year = rng.integers(1990, 2030, N)
    price = rng.integers(0, 4096, N)
    lang = rng.integers(0, 3, N)
    meta = [
        {"year": int(y), "price": int(p), "lang": int(g)}
        for y, p, g in zip(year, price, lang, strict=True)
    ]
    return x, meta


def _truth(x, meta, q, k, keep):
    idx = [i for i, m in enumerate(meta) if keep(m)]
    idx.sort(key=lambda i: -float(x[i] @ q))
    return [f"v{i}" for i in idx[:k]]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One collection for the whole module: encryption on CPU is slow."""
    tmp = tmp_path_factory.mktemp("ranges")
    x, meta = _data()
    client = ombra.Client.local(tmp / "data")
    client.create_collection(
        "c",
        x,
        [f"v{i}" for i in range(N)],
        key_file=tmp / "c.key",
        passphrase="pw",  # noqa: S106
        preset="fast",
        metadata=meta,
        filter_fields={"lang": 3},
        range_fields={"year": [1995, 2000, 2005, 2010, 2020, 2025], "price": {"kind": "two_digit"}},
        n_clusters=2,
        scrypt_params=(2**10, 8, 1),
    )
    yield tmp, client, x, meta
    client.close()


def test_range_round_trip(built):
    tmp, client, x, meta = built
    rng = np.random.default_rng(1)
    for _ in range(2):
        q = x[int(rng.integers(0, N))] + 0.05 * rng.standard_normal(D)
        q /= np.linalg.norm(q)
        cases = [
            ({"year": {"gte": 2003, "lt": 2018}}, lambda m: 2003 <= m["year"] < 2018),
            ({"year": 2007, "lang": 1}, lambda m: m["year"] == 2007 and m["lang"] == 1),
            ({"year": {"gt": 2024}, "lang": [0, 2]}, lambda m: m["year"] > 2024 and m["lang"] != 1),
            ({"price": {"gte": 300, "lte": 2500}}, lambda m: 300 <= m["price"] <= 2500),
            (
                {"price": {"gte": 300, "lte": 2500}, "lang": 2, "year": {"lte": 2015}},
                lambda m: 300 <= m["price"] <= 2500 and m["lang"] == 2 and m["year"] <= 2015,
            ),
            ({"lang": 1}, lambda m: m["lang"] == 1),
        ]
        for flt, keep in cases:
            hits = client.search("c", q, k=5, filter=flt)
            assert [h.id for h in hits] == _truth(x, meta, q, 5, keep), flt
            assert all(keep(h.metadata) for h in hits)


def test_reopen_reads_the_schema(built):
    tmp, client, x, meta = built
    c3 = ombra.Client.local(tmp / "data")
    c3.open_collection("c", key_file=tmp / "c.key", passphrase="pw", filter_fields=["lang"])  # noqa: S106
    hits = c3.search("c", x[0], k=3, filter={"year": {"gte": 2000, "lte": 2004}})
    assert all(2000 <= h.metadata["year"] <= 2004 for h in hits)
    c3.close()


def test_no_rerank_on_cut_points_fetches_nothing(built):
    """rerank=False: exact match set when the bounds sit on cut points, no record blob read,
    and a refusal (not a wrong answer) for any other range."""
    tmp, client, x, meta = built
    col, _ = client._core._get("c")
    calls = []
    real_get = col.blobs.get
    col.blobs.get = lambda *a, **kw: calls.append(a) or real_get(*a, **kw)
    try:
        rng = np.random.default_rng(2)
        q = x[int(rng.integers(0, N))] + 0.05 * rng.standard_normal(D)
        q /= np.linalg.norm(q)
        cases = [
            ({"year": {"gte": 2000, "lt": 2010}}, lambda m: 2000 <= m["year"] < 2010),
            ({"year": {"gte": 2020}, "lang": 1}, lambda m: m["year"] >= 2020 and m["lang"] == 1),
            ({"price": {"gte": 128, "lt": 2048}}, lambda m: 128 <= m["price"] < 2048),
            ({"lang": [0, 2]}, lambda m: m["lang"] != 1),
            (None, lambda m: True),
        ]
        for flt, keep in cases:
            hits = client.search("c", q, k=5, filter=flt, rerank=False)
            truth = _truth(x, meta, q, 5, keep)
            assert {h.id for h in hits} == set(truth), flt
            assert all(h.metadata == {} for h in hits)
            for h in hits:
                assert abs(h.score - float(x[int(h.id[1:])] @ q)) < 1e-3
        for flt in (
            {"year": {"gte": 2003, "lt": 2018}},
            {"year": 2007},
            {"price": {"gte": 128, "lt": 2048}, "lang": 1},
        ):
            with pytest.raises(ValueError, match="cut points"):
                client.search("c", q, k=5, filter=flt, rerank=False)
        assert calls == []
    finally:
        col.blobs.get = real_get
