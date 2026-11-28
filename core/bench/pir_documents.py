# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Cost of a private document call, per class: bytes up, bytes down, message counts (from the
code) and the time of the CPU reference answer.

    python core/bench/pir_documents.py [--k 10] [--n 10000] [--classes 1 2] [--no-time]

Bytes are measured, not assumed: the query ciphertexts are built by the client for the real shape
of an n-row database, and the size of one answer is taken from a real (tiny) reference call, since
every answer is one compressed ciphertext of the same size. The reference time is one call of
`PirDocumentsReference.answer` on n random blobs of the class (database build and answer apart).
Synthetic data only; nothing is printed but sizes, counts and times. CPU only.
"""

from __future__ import annotations

import argparse
import random
import time

import numpy as np
from ombra import pir
from ombra import pir_reference as ref

SEED = b"0123456789abcdef"


def _answer_size(client: pir.PirClient, keys: pir.EvalKeys) -> int:
    doc = pir.make_doc_shape(3, 2, 16384, SEED)
    c = client.with_shape(doc.shape)
    blobs = [bytes([i]) * 16384 for i in range(3)]
    server = ref.PirDocumentsReference(
        c.context_bytes(), keys.mult, keys.automorphism, pir.pack_doc_column(blobs, 1), 2, SEED
    )
    plan = pir.plan_batch(doc.shape, [0], random.Random(1))  # noqa: S311
    return len(server.answer(c.build_queries(plan))[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--n", type=int, default=10_000, help="blobs of the class in scope")
    ap.add_argument("--classes", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--no-time", action="store_true")
    args = ap.parse_args()
    client = pir.PirClient()
    keys = client.eval_keys()
    ans = _answer_size(client, keys)
    print(f"one compressed answer: {ans} bytes")
    for cls in args.classes:
        size = pir.DOC_CLASS_SIZES[cls]
        doc = pir.make_doc_shape(args.n, args.k, size, SEED)
        c = client.with_shape(doc.shape)
        plan = pir.plan_batch(doc.shape, [0], random.Random(1))  # noqa: S311
        up = [len(q) for q in c.build_queries(plan)]
        n_ans = doc.planes * doc.shape.buckets
        print(
            f"class {cls} ({size} B), k={args.k}, n={args.n}: planes={doc.planes} "
            f"buckets={doc.shape.buckets} grid={doc.shape.r1}x{doc.shape.r2} "
            f"| up: {sum(up)} B in {len(up)} messages | down: {n_ans * ans} B in {n_ans} messages"
        )
        if args.no_time:
            continue
        g = np.random.default_rng(cls)
        blobs = [g.integers(0, 256, size, dtype=np.uint8).tobytes() for _ in range(args.n)]
        t0 = time.perf_counter()
        server = ref.PirDocumentsReference(
            c.context_bytes(), keys.mult, keys.automorphism, pir.pack_doc_column(blobs, cls),
            args.k, SEED,
        )  # fmt: skip
        t1 = time.perf_counter()
        queries = c.build_queries(plan)
        t2 = time.perf_counter()
        answers = server.answer(queries)
        t3 = time.perf_counter()
        got = c.decode_document_answers(plan, answers, doc)
        t4 = time.perf_counter()
        assert all(blobs[r] == b for r, b in got.items()) and got
        print(
            f"  reference: setup {t1 - t0:.1f} s, queries {t2 - t1:.1f} s, "
            f"answer (expand + all planes) {t3 - t2:.1f} s, decode {t4 - t3:.1f} s"
        )


if __name__ == "__main__":
    main()
