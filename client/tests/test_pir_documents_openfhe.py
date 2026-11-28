# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private document retrieval over planes on OpenFHE (CPU): the reference server answers, the
client decodes, and every retrieved blob equals the stored one byte for byte.

Both practical classes (16 KiB: one plane, 256 KiB: 14 planes). Cases: fill rows, a cuckoo drop,
a scope of one row, and a scope with no document of the class (the padding row). Synthetic data
only; sizes are small so that the 256 KiB class (14 planes) still runs in a minute or so.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
import pytest
from ombra import pir
from ombra import pir_reference as ref

pytestmark = pytest.mark.openfhe
pytest.importorskip("openfhe")

SEED = b"0123456789abcdef"
SIZES = {1: 16384, 2: 262144}


def _rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311  # seeded on purpose: reproducible test plans


def _blobs(n: int, size: int, seed: int) -> list[bytes]:
    g = np.random.default_rng(seed)
    return [g.integers(0, 256, size, dtype=np.uint8).tobytes() for _ in range(n)]


@pytest.fixture(scope="module")
def keys():
    client = pir.PirClient()
    return client, client.eval_keys()


@dataclass
class Call:
    doc: pir.DocShape
    client: pir.PirClient
    server: ref.PirDocumentsReference
    blobs: list[bytes]


def make_call(keys, doc_class: int, blobs: list[bytes], m: int, seed: bytes = SEED) -> Call:
    client0, ek = keys
    size = SIZES[doc_class]
    doc = pir.make_doc_shape(len(blobs), m, size, seed)
    client = client0.with_shape(doc.shape)
    column = pir.pack_doc_column(blobs, doc_class)
    server = ref.PirDocumentsReference(
        client.context_bytes(), ek.mult, ek.automorphism, column, m, seed
    )
    assert server.shape == doc.shape  # the server derives the same shape from the column
    return Call(doc, client, server, blobs)


def fetch(call: Call, wanted: list[int], seed: int = 0):
    plan = pir.plan_batch(call.doc.shape, wanted, _rng(seed))
    queries = call.client.build_queries(plan)
    answers = call.server.answer(queries)
    assert len(answers) == call.doc.planes * call.doc.shape.buckets  # plane-major
    return plan, call.client.decode_document_answers(plan, answers, call.doc)


@pytest.mark.parametrize("doc_class", [1, 2])
def test_blobs_come_back_byte_for_byte_with_fill_rows(keys, doc_class: int) -> None:
    n = 40 if doc_class == 1 else 12
    blobs = _blobs(n, SIZES[doc_class], doc_class)
    call = make_call(keys, doc_class, blobs, m=4)
    plan, got = fetch(call, [n - 1, 3], seed=1)  # two wanted, two random real rows
    assert len(plan.filled) == 2 and not plan.dropped
    assert {n - 1, 3} <= set(got) and len(got) == 4
    for row, data in got.items():
        assert data == blobs[row]  # the stored bytes, filled rows included
    assert call.doc.planes == (1 if doc_class == 1 else 14)


def _triple_with_rows(n: int, m: int, count: int, seeds) -> tuple[bytes, list[int]]:
    buckets = pir.n_buckets(m)
    for s in seeds:
        seed = bytes([s]) * 16
        by: dict[tuple[int, ...], list[int]] = {}
        for i in range(n):
            by.setdefault(tuple(sorted(pir.candidate_buckets(seed, i, buckets))), []).append(i)
        best = max(by.values(), key=len)
        if len(best) >= count:
            return seed, best[:count]
    raise AssertionError("no seed gives rows with one bucket triple")


@pytest.mark.parametrize("doc_class", [1, 2])
def test_a_cuckoo_drop_is_counted_and_the_rest_decode(keys, doc_class: int) -> None:
    n = 60 if doc_class == 1 else 30
    seed, rows = _triple_with_rows(n, 4, 4, range(1, 400))  # 4 rows, only 3 buckets between them
    blobs = _blobs(n, SIZES[doc_class], 10 + doc_class)
    call = make_call(keys, doc_class, blobs, m=4, seed=seed)
    plan, got = fetch(call, rows)
    assert plan.dropped == [rows[3]]
    assert sorted(got) == sorted(rows[:3])  # no retry: the dropped row is simply absent
    for row, data in got.items():
        assert data == blobs[row]


@pytest.mark.parametrize("doc_class", [1, 2])
def test_a_scope_of_one_row(keys, doc_class: int) -> None:
    blob = _blobs(1, SIZES[doc_class], 20 + doc_class)
    call = make_call(keys, doc_class, blob, m=4)
    plan, got = fetch(call, [0])
    assert got == {0: blob[0]}
    assert call.doc.shape.n_rows == 1


@pytest.mark.parametrize("doc_class", [1, 2])
def test_a_scope_with_no_document_of_the_class_is_answered_over_a_padding_row(
    keys, doc_class: int
) -> None:
    size = SIZES[doc_class]
    client0, ek = keys
    empty = pir.pack_doc_column([], doc_class)
    doc = pir.make_doc_shape(0, 4, size, SEED)
    assert doc.shape.n_rows == 1
    server = ref.PirDocumentsReference(
        client0.context_bytes(), ek.mult, ek.automorphism, empty, 4, SEED
    )
    client = client0.with_shape(doc.shape)
    plan = pir.plan_batch(doc.shape, [], _rng(3))  # nothing wanted: the padding row is the fill
    assert plan.filled == [0]
    queries = client.build_queries(plan)
    answers = server.answer(queries)
    got = client.decode_document_answers(plan, answers, doc)
    assert got == {0: bytes(size)}  # the padding row is zeros: no blob can be mistaken for it
    # the call has the shape of a one-row scope, so the size of the call does not tell them apart
    one = make_call(keys, doc_class, _blobs(1, size, 5), m=4)
    assert one.doc.shape == doc.shape
    assert [len(q) for q in one.client.build_queries(plan)] == [len(q) for q in queries]
    assert len(one.server.answer(queries)) == len(answers)


def test_answer_sizes_do_not_depend_on_which_rows_are_wanted(keys) -> None:
    blobs = _blobs(30, SIZES[1], 7)
    call = make_call(keys, 1, blobs, m=4)
    sizes = set()
    for wanted in ([0], [5, 6, 7], [29, 1]):
        plan = pir.plan_batch(call.doc.shape, wanted, _rng(1))
        qs = call.client.build_queries(plan)
        ans = call.server.answer(qs)
        sizes.add((tuple(len(q) for q in qs), tuple(len(a) for a in ans)))
    assert len(sizes) == 1


def test_wrong_answer_count_is_refused(keys) -> None:
    blobs = _blobs(6, SIZES[1], 8)
    call = make_call(keys, 1, blobs, m=2)
    plan = pir.plan_batch(call.doc.shape, [1], _rng(0))
    answers = call.server.answer(call.client.build_queries(plan))
    with pytest.raises(pir.PirError):
        call.client.decode_document_answers(plan, answers[:-1], call.doc)
