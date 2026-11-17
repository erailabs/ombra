# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Integer range filters: encodings, decoding with the real band code, exactness.

The slot of a record is simulated as score + lambda * (query weights . one-hot columns), which is
what the server's dot product gives; no OpenFHE needed. Synthetic data only.
"""

from __future__ import annotations

import numpy as np
import pytest
from ombra import presets
from ombra.ranges import (
    BUCKETS,
    D_DOMAIN,
    RangeSchema,
    equal_width_edges,
    parse_bounds,
    quantile_edges,
)
from ombra.search import decode_band, filter_query

LAM = presets.FILTER_LAMBDA


def test_parse_bounds() -> None:
    assert parse_bounds(5) == (5, 5)
    assert parse_bounds({"gte": 3, "lt": 10}) == (3, 9)
    assert parse_bounds({"gt": 3, "lte": 10}) == (4, 10)
    lo, hi = parse_bounds({"gte": 7})
    assert lo == 7 and hi > 10**12
    for bad in ({}, {"eq": 1}, {"gte": 1.5}, {"gte": 5, "lte": 4}, "x", True):
        with pytest.raises(ValueError):
            parse_bounds(bad)


def test_edges_helpers() -> None:
    e = equal_width_edges(0, 99, 10)
    assert e == [10 * i for i in range(1, 10)]
    rng = np.random.default_rng(0)
    v = (rng.exponential(100, 20000)).astype(int)
    q = quantile_edges(v, 64)
    assert len(q) <= 63 and q == sorted(set(q))
    counts = np.bincount(np.searchsorted(q, v, side="right"))
    assert counts.max() < 2.5 * len(v) / 64


def test_bucket_mapping_and_cards() -> None:
    s = RangeSchema.from_spec({"year": [10, 20, 30]}, first_index=2, taken=("a",))
    f = s.get("year")
    assert [f.bucket_of(v) for v in (-5, 9, 10, 19, 20, 29, 30, 99)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert s.cards == (BUCKETS,)  # padded to 64 so the size does not stand out
    with pytest.raises(ValueError):
        RangeSchema.from_spec({"y": [5, 5]}, 0)
    with pytest.raises(ValueError):
        RangeSchema.from_spec({"y": list(range(64))}, 0)
    with pytest.raises(ValueError):
        RangeSchema.from_spec({"a": [1]}, 0, taken=("a",))
    d = RangeSchema.from_spec({"p": {"kind": "two_digit"}}, 0)
    assert d.cards == (64, 64) and d.n_fields == 2
    vals = d.bucket_values([{"p": 4095}, {"p": 70}], 2)
    assert vals.tolist() == [[63, 63], [1, 6]]
    with pytest.raises(ValueError):
        d.bucket_values([{"p": 4096}], 1)


def test_schema_blob_round_trip() -> None:
    s = RangeSchema.from_spec({"a": [1, 5], "b": {"kind": "two_digit"}}, first_index=1)
    t = RangeSchema.from_bytes(s.to_bytes())
    assert t == s and [f.index for f in t.fields] == [1, 2]
    bad = bytearray(s.to_bytes())
    bad[4] = 9  # version
    with pytest.raises(ValueError):
        RangeSchema.from_bytes(bytes(bad))


def _slots(schema, plan, eq_cards, eq_filter, eq_vals, raw, rng):
    """Simulate the server: slot = score + lambda * sum of query weights at each record's value."""
    cards = tuple(eq_cards) + schema.cards
    n = len(raw)
    fields = np.hstack([eq_vals, schema.bucket_values([{"x": int(v)} for v in raw], n)])
    q = np.zeros(4)
    q[0] = 1
    ext = filter_query(q, cards, eq_filter, plan.weights if plan else None)[4:]
    off = np.concatenate([[0], np.cumsum(cards)])
    units = np.zeros(n)
    for fi in range(len(cards)):
        units += ext[off[fi] + fields[:, fi]] / LAM
    score = rng.uniform(-1, 1, n)
    return score + LAM * units, score


@pytest.mark.parametrize("kind", ["buckets", "two_digit"])
@pytest.mark.parametrize("with_eq", [False, True])
def test_decode_and_exactness(kind, with_eq) -> None:
    """After the decoder and the exact check the matches are exactly the records in range
    (and, with an equality field, with the right value)."""
    rng = np.random.default_rng(3)
    n = 4000
    raw = rng.integers(0, D_DOMAIN, n)
    spec = {"x": [64 * i for i in range(1, 64)] if kind == "buckets" else {"kind": "two_digit"}}
    if kind == "buckets":  # edges not aligned with the queried bounds
        spec = {"x": quantile_edges(raw, 20)}
    schema = RangeSchema.from_spec(spec, first_index=1 if with_eq else 0)
    eq_cards = (5,) if with_eq else ()
    eq_vals = rng.integers(0, 5, (n, 1)) if with_eq else np.zeros((n, 0), dtype=np.int64)
    n_fields = len(eq_cards) + schema.n_fields
    for lo, hi in [
        (100, 100),
        (100, 150),
        (0, 63),
        (63, 64),
        (130, 2000),
        (0, D_DOMAIN - 1),
        (4000, 5000),
    ]:
        plan = schema.plan({"x": {"gte": lo, "lte": hi}})
        eqf = {0: [1, 3]} if with_eq else None
        slot, score = _slots(schema, plan, eq_cards, eqf, eq_vals, raw, rng)
        c, got_score = decode_band(slot)
        a, b = plan.accept(n_fields)
        np.testing.assert_allclose(got_score, score, atol=1e-9)
        cand = np.nonzero((c >= a) & (c <= b))[0]
        truth = (raw >= lo) & (raw <= hi)
        if with_eq:
            truth &= np.isin(eq_vals[:, 0], [1, 3])
        ok = [
            i
            for i in cand
            if plan.exact({"x": int(raw[i])}) and (not with_eq or eq_vals[i, 0] in (1, 3))
        ]
        assert sorted(ok) == sorted(np.nonzero(truth)[0])  # no true match is lost, none is added
        assert truth.sum() <= len(cand)


def test_digit_weights_units() -> None:
    """In range scores 2 or 3 units for the field; out of range at most 1 (single coarse bucket
    and adjacent buckets); with several buckets edge records can reach 2 and are removed later."""
    s = RangeSchema.from_spec({"p": {"kind": "two_digit"}}, 0)
    v = np.arange(D_DOMAIN)
    cf = np.stack([v // 64, v % 64], axis=1)
    for lo, hi in [(70, 90), (70, 200), (130, 500), (0, 4095), (64, 127), (5, 5)]:
        w = s.plan({"p": {"gte": lo, "lte": hi}}).weights
        total = w[0][cf[:, 0]] + w[1][cf[:, 1]]
        inside = (v >= lo) & (v <= hi)
        assert set(total[inside]) <= {2.0, 3.0}
        if hi // 64 - lo // 64 <= 1:  # one or two coarse buckets: clean separation
            assert total[~inside].max(initial=0) <= 1.0
        else:  # shared fine weights: edge records may reach 2, never 3
            assert total[~inside].max(initial=0) <= 2.0
    # the unfiltered field contributes 2 units (lambda on every coarse and fine value)
    q = np.zeros(4)
    q[0] = 1
    ext = filter_query(q, s.cards, None)[4:]
    assert ext.sum() == LAM * 128


def test_digit_headroom_counts_three_units() -> None:
    d = presets.derive("fast", 384)
    fmax = presets.filter_field_limit(d)
    s = RangeSchema.from_spec({"p": {"kind": "two_digit"}}, 0)
    s.check_headroom(d, 2)
    with pytest.raises(presets.ParamsError):
        s.check_headroom(d, fmax)  # fmax fields + 1 extra unit is over


@pytest.mark.parametrize("kind", ["buckets", "two_digit"])
@pytest.mark.parametrize("with_eq", [False, True])
def test_band_exact_means_no_record_is_needed(kind, with_eq) -> None:
    """When `band_exact` says so, the decoded band alone is the exact answer (search without
    re-ranking relies on this); when the bounds miss the cut points it is not, and it says no."""
    rng = np.random.default_rng(4)
    n = 4000
    raw = rng.integers(0, D_DOMAIN, n)
    edges = quantile_edges(raw, 20)
    spec = {"x": edges if kind == "buckets" else {"kind": "two_digit"}}
    schema = RangeSchema.from_spec(spec, first_index=1 if with_eq else 0)
    eq_cards = (5,) if with_eq else ()
    eq_vals = rng.integers(0, 5, (n, 1)) if with_eq else np.zeros((n, 0), dtype=np.int64)
    n_fields = len(eq_cards) + schema.n_fields
    cuts = edges if kind == "buckets" else [64 * i for i in range(1, 64)]
    aligned = [
        {"gte": cuts[2], "lt": cuts[9]},
        {"gte": cuts[5]},
        {"lt": cuts[3]},
        {"gte": cuts[0], "lt": cuts[1]},
    ]
    if kind == "two_digit":
        aligned += [{"gte": 0, "lte": D_DOMAIN - 1}, {"gte": 128, "lte": 191}]
    unaligned = [{"gte": cuts[2] + 1, "lt": cuts[9]}, {"gte": 100, "lte": 2000}, 1234]
    eqf = {0: [1, 3]} if with_eq else None
    lost_somewhere = False
    for want in aligned + unaligned:
        plan = schema.plan({"x": want})
        lo, hi = parse_bounds(want)
        slot, _ = _slots(schema, plan, eq_cards, eqf, eq_vals, raw, rng)
        c, _ = decode_band(slot)
        a, b = plan.accept(n_fields)
        band = set(np.nonzero((c >= a) & (c <= b))[0].tolist())
        truth = (raw >= lo) & (raw <= hi)
        if with_eq:
            truth &= np.isin(eq_vals[:, 0], [1, 3])
        truth_set = set(np.nonzero(truth)[0].tolist())
        exact = plan.band_exact(1 if with_eq else 0)
        if want in aligned:
            assert exact == (kind == "buckets" or not with_eq), want
        else:
            assert not exact, want
        if exact:
            assert band == truth_set, want
        lost_somewhere |= band != truth_set
    assert lost_somewhere  # some answers do need the records, so the refusal is not idle
