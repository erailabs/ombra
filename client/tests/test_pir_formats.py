# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private retrieval: row and column formats, layout, hashing, shape, batch planning.

No OpenFHE needed (the HE tests are in test_pir_openfhe.py). Synthetic data only.
"""

from __future__ import annotations

import ast
import random
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from ombra import pir
from ombra.pir import PirError

KEY = bytes(range(32))
SEED = b"0123456789abcdef"


def _rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311  # seeded on purpose: reproducible test plans


def codec(dim: int = 768, n_ints: int = 4) -> pir.RowCodec:
    return pir.RowCodec(KEY, "coll", dim, n_ints)


def _vec(dim: int, k: int = 0) -> np.ndarray:
    return np.random.default_rng(k).standard_normal(dim).astype("<f4")


# ---------------------------------------------------------------- rows


def test_row_size_at_d768() -> None:
    assert codec(768, 4).row_size == 3136  # 4d + 8R + 28 = 3132, padded to 64
    for d, r in [(1, 0), (8, 2), (384, 3), (1536, 4)]:
        s = pir.row_size_for(d, r)
        assert s % 64 == 0 and 4 * d + 8 * r + 28 <= s < 4 * d + 8 * r + 28 + 64


def test_row_round_trip() -> None:
    c = codec()
    v = _vec(768)
    row = c.seal(3, b"rec-1", 7, v, [1, -2, 2**40, -(2**62)])
    assert len(row) == c.row_size
    got_v, got_i = c.open(row, 3, b"rec-1", 7)
    assert np.array_equal(got_v, v) and got_i.tolist() == [1, -2, 2**40, -(2**62)]


def test_row_sealing_is_randomised() -> None:
    c = codec(8, 1)
    a = c.seal(0, b"x", 1, _vec(8), [0])
    b = c.seal(0, b"x", 1, _vec(8), [0])
    assert a != b


@pytest.mark.parametrize(
    "cluster,rid,version",
    [(4, b"rec-1", 7), (3, b"rec-2", 7), (3, b"rec-1", 8)],
)
def test_row_wrong_aad_fails(cluster: int, rid: bytes, version: int) -> None:
    c = codec(16, 1)
    row = c.seal(3, b"rec-1", 7, _vec(16), [5])
    with pytest.raises(PirError):
        c.open(row, cluster, rid, version)
    other = pir.RowCodec(KEY, "other", 16, 1)
    with pytest.raises(PirError):
        other.open(row, 3, b"rec-1", 7)


def test_row_wrong_key_tag_or_padding_fails() -> None:
    c = codec(16, 1)
    row = c.seal(0, b"r", 1, _vec(16), [5])
    with pytest.raises(PirError):
        pir.RowCodec(bytes(32), "coll", 16, 1).open(row, 0, b"r", 1)
    end = c._sealed_len
    for pos in (0, 12, end - 1):  # nonce, ciphertext, last tag byte
        bad = bytearray(row)
        bad[pos] ^= 1
        with pytest.raises(PirError):
            c.open(bytes(bad), 0, b"r", 1)
    if end < len(row):
        bad = bytearray(row)
        bad[end] = 1
        with pytest.raises(PirError):
            c.open(bytes(bad), 0, b"r", 1)
    with pytest.raises(PirError):
        c.open(row[:-1], 0, b"r", 1)


def test_row_input_checks() -> None:
    with pytest.raises(PirError):
        pir.RowCodec(b"short", "c", 4, 0)
    with pytest.raises(PirError):
        codec(4, 1).seal(0, b"r", 1, _vec(5), [1])
    assert "RowCodec(<redacted>)" == repr(codec())


# ---------------------------------------------------------------- column


def _column(n: int = 5, dim: int = 16) -> tuple[bytes, list[bytes], pir.RowCodec]:
    c = codec(dim, 1)
    rows = [c.seal(i, b"id%d" % i, 1, _vec(dim, i), [i]) for i in range(n)]
    return pir.pack_column(rows, c.row_size), rows, c


def test_column_round_trip() -> None:
    data, rows, c = _column()
    assert data[:4] == pir.COLUMN_MAGIC
    assert pir.COLUMN_VERSION == 1
    col = pir.parse_column(data)
    assert (col.row_size, col.n_rows) == (c.row_size, 5)
    assert [col.row(i) for i in range(5)] == rows
    with pytest.raises(IndexError):
        col.row(5)
    assert pir.parse_column(pir.pack_column([], 64)).n_rows == 0


def test_column_rejects_bad_input() -> None:
    data, rows, c = _column()
    version_at = 4  # magic is 4 bytes, then the u16 version
    bad_version = data[:version_at] + (2).to_bytes(2, "little") + data[version_at + 2 :]
    with pytest.raises(PirError, match="version"):
        pir.parse_column(bad_version)
    for cut in range(pir.COLUMN_HEADER_LEN):  # every truncated header
        with pytest.raises(PirError, match="truncated"):
            pir.parse_column(data[:cut])
    with pytest.raises(PirError, match="not a record column"):
        pir.parse_column(b"XXXX" + data[4:])
    with pytest.raises(PirError, match="length"):
        pir.parse_column(data[:-1])
    with pytest.raises(PirError, match="length"):
        pir.parse_column(data + b"\0")
    reserved = data[:6] + (1).to_bytes(2, "little") + data[8:]
    with pytest.raises(PirError, match="malformed"):
        pir.parse_column(reserved)
    with pytest.raises(PirError):
        pir.pack_column([b"x" * 63], 63)
    with pytest.raises(PirError):
        pir.pack_column([rows[0][:-1]], c.row_size)


# ---------------------------------------------------------------- layout


def test_plaintext_layout_numbers() -> None:
    pp = pir.preset_params()
    assert (pp.ring_dim, pp.plain_modulus, pp.coeff_bits) == (8192, 786433, 19)
    assert pp.plaintext_bytes == 19456
    assert pir.rows_per_plaintext(codec(768, 4).row_size, pp) == 6
    with pytest.raises(PirError):
        pir.rows_per_plaintext(19456 + 64, pp)


def test_coeff_packing_round_trip() -> None:
    pp = pir.preset_params()
    rng = np.random.default_rng(1)
    for n in (0, 1, 100, pp.plaintext_bytes):
        data = rng.bytes(n)
        c = pir.bytes_to_coeffs(data, pp)
        assert c.shape == (pp.ring_dim,) and 0 <= c.min() and c.max() < 2**19 < pp.plain_modulus
        assert pir.coeffs_to_bytes(c, pp)[:n] == data
    with pytest.raises(PirError):
        pir.bytes_to_coeffs(bytes(pp.plaintext_bytes + 1), pp)
    with pytest.raises(PirError):  # a value that no stored plaintext can hold
        pir.coeffs_to_bytes([2**19] * pp.ring_dim, pp)


# ---------------------------------------------------------------- hashing


def test_candidates_are_distinct_deterministic_and_keyed() -> None:
    for buckets in (3, 4, 6, 45):
        for i in range(200):
            c = pir.candidate_buckets(SEED, i, buckets)
            assert len(set(c)) == 3 and all(0 <= b < buckets for b in c)
            assert c == pir.candidate_buckets(SEED, i, buckets)
    other = [pir.candidate_buckets(b"another-seed-16b", i, 45) for i in range(50)]
    assert other != [pir.candidate_buckets(SEED, i, 45) for i in range(50)]
    with pytest.raises(PirError):
        pir.candidate_buckets(SEED, 0, 2)


def test_every_row_is_in_three_buckets() -> None:
    contents = pir.bucket_contents(SEED, 100, 15)
    assert Counter(i for b in contents for i in b) == Counter(dict.fromkeys(range(100), 3))
    assert all(b == sorted(b) for b in contents)


def test_bucket_count() -> None:
    assert [pir.n_buckets(m) for m in (1, 2, 4, 30, 120, 3000)] == [3, 3, 6, 45, 180, 4500]


# ---------------------------------------------------------------- shape


def test_shape_numbers_at_d768() -> None:
    row_size = codec(768, 4).row_size
    s = pir.make_shape(40, 4, row_size, SEED)
    assert (s.buckets, s.rows_per_pt) == (6, 6)
    assert s.r1 * s.r2 >= s.pts_per_bucket == -(-s.bucket_rows // 6)
    assert s.per_ct * s.coeffs_per_bucket <= s.pp.ring_dim
    assert 2**s.levels >= s.per_ct * s.coeffs_per_bucket > 2 ** (s.levels - 1)
    assert s.n_cts * s.per_ct >= s.buckets
    idx = s.eval_automorphism_indices()
    assert idx == [8192 // 2**j + 1 for j in range(s.levels)]


def test_shape_depends_only_on_public_inputs() -> None:
    rs = codec(64, 2).row_size
    a = pir.make_shape(300, 4, rs, SEED)
    b = pir.make_shape(300, 4, rs, SEED)
    assert a == b
    # the plan, whatever it selects, never changes the shape
    shapes = {
        (p.shape.buckets, p.shape.r1, p.shape.r2, p.shape.n_cts, p.shape.levels)
        for p in (pir.plan_batch(a, c, _rng(0)) for c in ([], [1], [1, 2, 3, 4], list(range(50))))
    }
    assert len(shapes) == 1


def test_large_shape_grids() -> None:
    s = pir.make_shape(1200, 30, codec(768, 4).row_size, SEED)
    assert s.buckets == 45 and s.rows_per_pt == 6
    assert s.levels <= 13 and s.r1 + s.r2 <= 2 * (s.pts_per_bucket**0.5 + 1)


# ---------------------------------------------------------------- batch planning


def _shape(n: int = 300, m: int = 4) -> pir.PirShape:
    return pir.make_shape(n, m, codec(64, 2).row_size, SEED)


def test_plan_places_each_row_in_a_candidate_bucket() -> None:
    s = _shape(300, 10)
    plan = pir.plan_batch(s, [3, 1, 4, 15, 9, 26, 53, 58], _rng(1))
    assert len(plan.assignment) == s.buckets
    placed = [r for r in plan.assignment if r is not None]
    assert sorted(placed) == sorted(plan.targets)
    for b, r in enumerate(plan.assignment):
        if r is not None:
            assert b in pir.candidate_buckets(SEED, r, s.buckets)
    for b, r in enumerate(plan.assignment):
        a, c = plan.cell(b)
        assert 0 <= a < s.r1 and 0 <= c < s.r2
        if r is None:
            assert b in plan.dummy_cells


def test_plan_fills_to_m_with_random_real_rows() -> None:
    s = _shape(300, 10)
    plan = pir.plan_batch(s, [7, 7, 8], _rng(2))  # a duplicate counts once
    assert len(plan.targets) + len(plan.dropped) == 10
    assert len(plan.filled) == 8 and not set(plan.filled) & {7, 8}
    assert all(0 <= r < 300 for r in plan.filled)
    again = pir.plan_batch(s, [7, 7, 8], _rng(3))
    assert again.filled != plan.filled  # random fill


def test_plan_cuts_to_m_and_keeps_rank_order() -> None:
    s = _shape(300, 4)
    plan = pir.plan_batch(s, list(range(40)), _rng(0))
    assert len(plan.targets) + len(plan.dropped) == 4
    assert set(plan.targets + plan.dropped) == {0, 1, 2, 3}
    assert not plan.filled


def test_plan_fill_is_capped_by_database_size() -> None:
    s = pir.make_shape(3, 4, codec(64, 2).row_size, SEED)
    plan = pir.plan_batch(s, [0], _rng(0))
    assert len(plan.targets) + len(plan.dropped) == 3


def test_plan_rejects_out_of_range() -> None:
    with pytest.raises(PirError):
        pir.plan_batch(_shape(), [300])
    with pytest.raises(PirError):
        pir.plan_batch(_shape(), [-1])


def crowded_rows(s: pir.PirShape, k: int) -> list[int]:
    """k rows whose three candidate buckets are the same triple (k > 3 cannot all be placed)."""
    by_triple: dict[tuple[int, ...], list[int]] = {}
    for i in range(s.n_rows):
        by_triple.setdefault(tuple(sorted(pir.candidate_buckets(s.seed, i, s.buckets))), []).append(
            i
        )
    rows = max(by_triple.values(), key=len)
    assert len(rows) >= k, "test seed gives no crowded triple"
    return rows[:k]


def test_cuckoo_failure_drops_lowest_ranked_and_counts_it() -> None:
    s = _shape(300, 4)
    rows = crowded_rows(s, 4)
    plan = pir.plan_batch(s, rows, _rng(0))
    assert plan.dropped == [rows[3]]  # the lowest-ranked one
    assert plan.targets == rows[:3]
    assert sum(r is not None for r in plan.assignment) == 3
    assert not plan.filled
    # best-ranked rows win: reverse the ranking and the other end is dropped
    plan2 = pir.plan_batch(s, rows[::-1], _rng(0))
    assert plan2.dropped == [rows[0]]


def test_cuckoo_moves_rows_to_make_room() -> None:
    # Crowd three rows into one triple, then a fourth that shares one of its buckets: the plan
    # must still place all four when a perfect assignment exists.
    s = _shape(300, 4)
    for extra in range(300):
        rows = crowded_rows(s, 3)
        if extra in rows:
            continue
        cands = [set(pir.candidate_buckets(SEED, r, s.buckets)) for r in rows + [extra]]
        if len(set.union(*cands)) >= 4 and cands[3] & cands[0]:
            plan = pir.plan_batch(s, rows + [extra], _rng(0))
            assert not plan.dropped and len(plan.targets) == 4
            return
    pytest.fail("no example found")


# ---------------------------------------------------------------- structure


def test_reference_server_cannot_touch_the_secret_key() -> None:
    src = (Path(pir.__file__).with_name("pir_reference.py")).read_text()
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name)
    banned = {
        "Decrypt",
        "KeyGen",
        "secretKey",
        "PrivateKey",
        "PirClient",
        "RowCodec",
        "EvalMultKeyGen",
    }
    banned |= {
        "EvalAutomorphismKeyGen",
        "DeserializePrivateKey",
        "GetPrivateKey",
        "make_bfv_context",
    }
    assert not names & banned
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imports <= {"__future__", "collections.abc", "typing", "ombra.pir"}
    from_pir = {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module == "ombra.pir"
        for a in n.names
    }
    assert from_pir <= {"PirError", "PirShape", "bytes_to_coeffs", "make_shape", "parse_column"}
