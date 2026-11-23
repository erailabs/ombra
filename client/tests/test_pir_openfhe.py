# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private retrieval on OpenFHE (CPU): client queries, the reference server answer, decoding.

Synthetic data only. The server (`ombra.pir_reference`) is built from serialized public bytes
alone. Sizes are small so the tests run in seconds; the noise test uses the full 13 expansion
levels on a 12 x 12 grid, the worst shape of the protocol.
"""

from __future__ import annotations

import dataclasses
import random
import time
from dataclasses import dataclass

import numpy as np
import pytest
from ombra import pir
from ombra import pir_reference as ref

pytestmark = pytest.mark.openfhe
pytest.importorskip("openfhe")

KEY = bytes(range(32))
SEED = b"0123456789abcdef"


def _rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311  # seeded on purpose: reproducible test plans


@dataclass
class Setup:
    codec: pir.RowCodec
    vecs: list[np.ndarray]
    rows: list[bytes]
    shape: pir.PirShape
    client: pir.PirClient
    keys: pir.EvalKeys
    server: ref.PirServerReference
    key_indices: set[int]
    keygen_s: float
    build_s: float

    def meta(self, i: int) -> tuple[int, bytes, int]:
        return i % 3, b"id%d" % i, 1


def setup(dim: int, n_ints: int, n: int, m: int, levels: int | None = None) -> Setup:
    codec = pir.RowCodec(KEY, "coll", dim, n_ints)
    rng = np.random.default_rng(dim + n)
    vecs = [rng.standard_normal(dim).astype("<f4") for _ in range(n)]
    rows = [codec.seal(i % 3, b"id%d" % i, 1, vecs[i], [i * 7 - 5] * n_ints) for i in range(n)]
    column = pir.pack_column(rows, codec.row_size)
    shape = pir.make_shape(n, m, codec.row_size, SEED)
    if levels is not None:
        shape = dataclasses.replace(shape, levels=levels)
    t0 = time.perf_counter()
    client = pir.PirClient(shape)
    keys = client.eval_keys()
    keygen_s = time.perf_counter() - t0
    tag = client._keys.publicKey.GetKeyTag()
    key_indices = {int(k) for k in client.cc.GetExistingEvalAutomorphismKeyIndices(tag)}
    t0 = time.perf_counter()
    server = ref.PirServerReference(
        client.context_bytes(), keys.mult, keys.automorphism, column, m, SEED
    )
    if levels is not None:
        server.shape = dataclasses.replace(server.shape, levels=levels)
    build_s = time.perf_counter() - t0
    return Setup(codec, vecs, rows, shape, client, keys, server, key_indices, keygen_s, build_s)


@pytest.fixture(scope="module")
def big() -> Setup:  # d = 768: 6 rows per plaintext
    return setup(768, 4, 40, 4)


@pytest.fixture(scope="module")
def small() -> Setup:  # d = 64: many rows per plaintext, fast
    return setup(64, 2, 300, 4)


def retrieve(
    s: Setup, candidates: list[int], seed: int = 0
) -> tuple[pir.BatchPlan, dict[int, bytes]]:
    plan = pir.plan_batch(s.shape, candidates, _rng(seed))
    queries = s.client.build_queries(plan)
    answers = s.server.answer(queries)
    return plan, s.client.decode_answers(plan, answers)


def check_rows(s: Setup, got: dict[int, bytes]) -> None:
    for i, row in got.items():
        assert row == s.rows[i]  # byte for byte
        v, ints = s.codec.open(row, *s.meta(i))
        assert np.array_equal(v, s.vecs[i])
        assert ints.tolist() == [i * 7 - 5] * s.codec.n_ints


# ---------------------------------------------------------------- retrieval


def test_rows_byte_for_byte_first_last_and_plaintext_boundary(big: Setup) -> None:
    sh = big.shape
    rpp = sh.rows_per_pt
    assert rpp == 6
    # A row at the last slot of a plaintext and the row after it, in the next plaintext, from
    # their primary buckets. Rows never span two plaintexts, so this is the boundary there is.
    last_slot = after = None
    primaries: dict[int, list[tuple[int, int]]] = {}
    for b, rows in enumerate(sh.contents):
        for rank, row in enumerate(rows):
            if pir.candidate_buckets(sh.seed, row, sh.buckets)[0] == b:
                primaries.setdefault(b, []).append((row, rank))
    for b, lst in primaries.items():
        for row, rank in lst:
            if row in (0, sh.n_rows - 1):
                continue
            if rank % rpp == rpp - 1 and last_slot is None:
                last_slot = (b, row)
            if (
                rank % rpp == 0
                and rank >= rpp
                and after is None
                and (last_slot is None or b != last_slot[0])
            ):
                after = (b, row)
    assert last_slot and after
    wanted = [last_slot[1], after[1], 0, sh.n_rows - 1]
    plan, got = retrieve(big, wanted)
    assert not plan.dropped and sorted(got) == sorted(wanted)
    assert plan.assignment[last_slot[0]] == last_slot[1]
    assert plan.assignment[after[0]] == after[1]
    check_rows(big, got)
    assert plan.targets == wanted


def test_single_row_retrievals_across_the_column(small: Setup) -> None:
    n = small.shape.n_rows
    for start in range(0, n, 4 * 20):  # a spread of single-row retrievals, one batch each
        plan, got = retrieve(small, [start % n], seed=start)
        check_rows(small, {start % n: got[start % n]})


def test_batch_fills_to_m_with_random_real_rows(small: Setup) -> None:
    plan, got = retrieve(small, [10, 20], seed=5)
    assert len(plan.targets) + len(plan.dropped) == small.shape.m
    assert len(plan.filled) == small.shape.m - 2
    assert {10, 20} <= set(got) | set(plan.dropped)
    check_rows(small, got)
    assert set(got) == set(plan.targets)


def test_cuckoo_failure_drops_one_and_answers_the_rest(small: Setup) -> None:
    sh = small.shape
    by_triple: dict[tuple[int, ...], list[int]] = {}
    for i in range(sh.n_rows):
        by_triple.setdefault(tuple(sorted(pir.candidate_buckets(SEED, i, sh.buckets))), []).append(
            i
        )
    rows = max(by_triple.values(), key=len)[:4]
    plan, got = retrieve(small, rows)
    assert plan.dropped == [rows[3]]
    assert sorted(got) == sorted(rows[:3])  # one call, no retry; the dropped row is absent
    check_rows(small, got)


def test_wrong_query_count_is_refused(small: Setup) -> None:
    with pytest.raises(pir.PirError):
        small.server.answer([])


# ---------------------------------------------------------------- fixed shape


def test_fixed_shape_does_not_depend_on_the_selection(small: Setup) -> None:
    sh = small.shape
    crowded = max(
        (
            [
                i
                for i in range(sh.n_rows)
                if tuple(sorted(pir.candidate_buckets(SEED, i, sh.buckets))) == t
            ]
            for t in {
                tuple(sorted(pir.candidate_buckets(SEED, i, sh.buckets))) for i in range(sh.n_rows)
            }
        ),
        key=len,
    )[:4]
    selections = [[], [0], [299, 150, 7, 8], crowded, list(range(100, 140))]
    seen = set()
    for k, sel in enumerate(selections):
        plan = pir.plan_batch(sh, sel, _rng(k))
        queries = small.client.build_queries(plan)
        answers = small.server.answer(queries)
        seen.add(
            (
                len(queries),
                tuple(len(q) for q in queries),
                len(plan.assignment),
                len(answers),
                tuple(len(a) for a in answers),
            )
        )
    assert len(seen) == 1
    ((n_q, q_sizes, n_buckets, n_a, a_sizes),) = seen
    assert n_q == sh.n_cts and n_buckets == n_a == sh.buckets


def test_shape_is_a_function_of_m_and_database_size(small: Setup) -> None:
    again = pir.make_shape(300, 4, small.codec.row_size, SEED)
    assert (again.buckets, again.r1, again.r2, again.n_cts, again.levels) == (
        small.shape.buckets,
        small.shape.r1,
        small.shape.r2,
        small.shape.n_cts,
        small.shape.levels,
    )


# ---------------------------------------------------------------- keys


def test_evaluation_keys_are_exactly_the_needed_ones(big: Setup, small: Setup) -> None:
    for s in (big, small):
        assert s.key_indices == set(s.shape.eval_automorphism_indices())
        assert len(s.key_indices) == s.shape.levels
        assert len(s.keys.mult) > 0 and len(s.keys.automorphism) > 0
        # each key is about 3.1 MB at digit 10; the totals are recorded by the noise test
        per_key = len(s.keys.automorphism) / s.shape.levels
        assert 2.5e6 < per_key < 4e6


def test_server_object_holds_no_key_material_but_what_it_was_given(big: Setup) -> None:
    attrs = {k for k in vars(big.server)}
    assert not any("secret" in k.lower() or "private" in k.lower() for k in attrs)


# ---------------------------------------------------------------- noise


def test_noise_budget_at_digit_10_full_depth() -> None:
    # 864 rows at d = 768 with m = 2: every bucket holds every row, a 12 x 12 grid. The levels are
    # raised to 13 (the depth of a large call): the noise depends on the number of levels, not on
    # how many selectors are used, and the expansion is pruned to the 72 that are needed.
    s = setup(768, 4, 864, 2, levels=13)
    assert (s.shape.r1, s.shape.r2, s.shape.levels) == (12, 12, 13)
    assert s.shape.eval_automorphism_indices() == [8192 // 2**j + 1 for j in range(13)]
    rows = [0, 863]
    plan = pir.plan_batch(s.shape, rows, _rng(0))
    queries = s.client.build_queries(plan)
    t0 = time.perf_counter()
    answers = s.server.answer(queries)
    answer_s = time.perf_counter() - t0
    got = s.client.decode_answers(plan, answers)
    assert sorted(got) == rows
    check_rows(s, got)

    pp, sh = s.shape.pp, s.shape
    budgets = []
    for b, row in enumerate(plan.assignment):
        if row is None:
            continue
        a, c = plan.cell(b)
        p = a * sh.r2 + c
        chunk = sh.contents[b][p * sh.rows_per_pt : (p + 1) * sh.rows_per_pt]
        data = b"".join(s.rows[i] for i in chunk)
        budgets.append(s.client.noise_budget(answers[b], pir.bytes_to_coeffs(data, pp)))
    print(
        f"\nPIR noise budget at digit 10, L=13, 12x12: {budgets} bits;"
        f" eval keys: mult {len(s.keys.mult)} B, automorphism {len(s.keys.automorphism)} B"
        f" ({len(s.keys)} B total); query {len(queries[0])} B x {len(queries)};"
        f" answer {len(answers[0])} B x {len(answers)};"
        f" keygen {s.keygen_s:.1f}s, db build {s.build_s:.1f}s, answer {answer_s:.1f}s"
    )
    assert budgets and min(budgets) >= 10


def test_noise_budget_helper_detects_a_wrong_plaintext(small: Setup) -> None:
    plan = pir.plan_batch(small.shape, [3], _rng(0))
    answers = small.server.answer(small.client.build_queries(plan))
    b = next(i for i, r in enumerate(plan.assignment) if r == 3)
    wrong = np.zeros(small.shape.pp.ring_dim, dtype=np.int64)
    assert small.client.noise_budget(answers[b], wrong) == -1


def test_client_and_reference_do_not_expose_secrets(big: Setup) -> None:
    assert repr(big.client) == "PirClient(<redacted>)"
    assert isinstance(big.server.cc, type(big.client.cc))
    assert not any(isinstance(v, bytes) and v == KEY for v in vars(big.server).values())


def _policy_limit(text: str, name: str) -> int:
    import re

    m = re.search(rf"kMaxPir{name}Bytes = std::size_t{{(\d+)}} << (\d+);", text)
    assert m, name
    return int(m.group(1)) << int(m.group(2))


def test_key_files_are_enveloped_and_fit_the_server_limits() -> None:
    """The three public key files, in file envelopes, at the largest shape (13 expansion levels,
    so the most automorphism keys). Measured: about 571 B, 3.1 MB and 41 MB; the server's limits
    (read from its policy header) must leave a margin."""
    from pathlib import Path

    from ombra.file_format import Kind, read_envelope

    shape = dataclasses.replace(pir.make_shape(1000, 3000, 256, SEED), levels=13)
    files = pir.PirClient(shape).key_files("1.6.1")
    want = {
        "pir_context": Kind.CONTEXT,
        "pir_eval_mult": Kind.PUBLIC_EVAL_KEY,
        "pir_automorphism": Kind.PUBLIC_EVAL_KEY,
    }
    assert set(files) == set(want)
    for name, raw in files.items():
        env = read_envelope(raw, want[name], pir.PIR_PRESET, "1.6.1")
        assert env.kind == want[name]
        assert len(env.payload) > 0
    policy = Path(__file__).resolve().parents[2] / "server/grpc/include/ombra/server/policy.hpp"
    if policy.exists():
        text = policy.read_text()
        assert len(files["pir_context"]) * 100 < _policy_limit(text, "Context")
        assert len(files["pir_eval_mult"]) * 4 < _policy_limit(text, "EvalMult")
        assert len(files["pir_automorphism"]) * 4 < _policy_limit(text, "Automorphism")
