# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Writes the golden fixtures of the C++ retrieval layout from the Python code (client/ombra/pir.py)
and BLAKE2b from hashlib. tests/test_pir_layout_golden.py regenerates them and compares, so the
checked-in files cannot drift from the client.

    python server/pir/tests/generate_golden.py   # rewrites the files in golden/

Synthetic data only. Line formats are documented in the C++ test that reads them.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"

# (n_rows, m, row_size, seed): small calls that cover one-plaintext buckets, several plaintexts per
# bucket (r1 > 1) and several query ciphertexts.
SHAPE_CASES = [
    (1, 1, 64, b"0123456789abcdef"),
    (10, 1, 64, b"\x00" * 16),
    (40, 2, 256, b"seed-seed-seed-ab"[:16]),
    (200, 3, 1024, bytes(range(16))),
    (500, 3, 3200, b"\xff" * 16),
    (700, 1, 6400, b"ombra-pir-golden!"[:16]),
    (6000, 2000, 6400, b"many-buckets-128"),  # 3000 buckets, 2 query ciphertexts
]
# (doc_class, n_rows, m, seed): the planes of the document classes. Class 2 has 14 planes (the last
# one is the 9,216-byte tail zero padded to 19,456); 0 rows is the one padding row of zeros.
DOC_CASES = [
    (1, 3, 2, b"0123456789abcdef"),
    (1, 0, 1, b"\x00" * 16),
    (2, 3, 2, bytes(range(16))),
    (2, 5, 3, b"\xff" * 16),
    (2, 0, 1, b"ombra-pir-golden!"[:16]),
]
HASH_CASES = [(b"0123456789abcdef", 3), (bytes(range(16)), 7), (b"\xaa" * 16, 4500)]
COEFF_LENGTHS = [0, 1, 7, 64, 1000, 19456]


def _pattern(n: int) -> bytes:
    return bytes((i * 37 + 5) % 256 for i in range(n))


def layout_text() -> str:
    sys.path.insert(0, str(HERE.parents[2] / "client"))
    from ombra import pir

    pp = pir.preset_params()
    out = [f"params {pp.ring_dim} {pp.plain_modulus}"]
    for seed, buckets in HASH_CASES:
        rows = [0, 1, 2, 3, 255, 256, 65535, 2**32 + 5, 2**40 + 123456789]
        out.append(f"hash {seed.hex()} {buckets} {len(rows)}")
        for r in rows:
            out.append(f"h {r} " + " ".join(map(str, pir.candidate_buckets(seed, r, buckets))))
    for n_rows, m, row_size, seed in SHAPE_CASES:
        s = pir.make_shape(n_rows, m, row_size, seed)
        out.append(f"shape {n_rows} {m} {row_size} {seed.hex()}")
        out.append(
            "f "
            + " ".join(
                map(
                    str,
                    [
                        s.buckets,
                        s.rows_per_pt,
                        s.bucket_rows,
                        s.pts_per_bucket,
                        s.r1,
                        s.r2,
                        s.per_ct,
                        s.n_cts,
                        s.levels,
                        s.coeffs_per_bucket,
                    ],
                )
            )
            + " | "
            + " ".join(map(str, s.eval_automorphism_indices()))
            + " | "
            + " ".join(str(s.buckets_in_ct(c)) for c in range(s.n_cts))
        )
        for b, rows in enumerate(s.contents):
            if s.buckets > 200 and b not in (0, 1, 2, s.buckets - 1):
                continue  # a large call lists a few buckets only
            out.append(f"c {b} " + " ".join(map(str, rows)))
    for n in COEFF_LENGTHS:
        c = pir.bytes_to_coeffs(_pattern(n), pp)
        weighted = sum((i + 1) * int(v) for i, v in enumerate(c))
        out.append(
            f"coeffs {n} {int(c.sum())} {weighted} " + " ".join(str(int(v)) for v in c[:24])
            + " | " + " ".join(str(int(v)) for v in c[-8:])
        )  # fmt: skip
    return "\n".join(out) + "\n"


def _doc_row(r: int, size: int) -> bytes:
    """Synthetic document r of a class: a pattern that differs per row and per byte."""
    return bytes((r * 131 + j * 7 + 3) % 256 for j in range(size))


def documents_text() -> str:
    """Per case: the shape shared by the planes and the BLAKE2b-256 of every plane's database
    (the chunk p of each row, zero padded, rows back to back), from `ombra.pir.plane_chunk`."""
    sys.path.insert(0, str(HERE.parents[2] / "client"))
    from ombra import pir

    out = []
    for cls, n_rows, m, seed in DOC_CASES:
        size = pir.DOC_CLASS_SIZES[cls]
        rows = [_doc_row(r, size) for r in range(n_rows)] or [bytes(size)]
        doc = pir.make_doc_shape(len(rows), m, size, seed)
        s = doc.shape
        out.append(f"doc {cls} {size} {n_rows} {m} {seed.hex()}")
        out.append(
            f"f {doc.planes} {s.row_size} {s.n_rows} {s.buckets} {s.n_cts} {s.r1} {s.r2} "
            f"{s.rows_per_pt}"
        )
        for p in range(doc.planes):
            body = b"".join(pir.plane_chunk(r, p, size) for r in rows)
            out.append(f"p {p} {hashlib.blake2b(body, digest_size=32).hexdigest()}")
    return "\n".join(out) + "\n"


def blake2b_text() -> str:
    out = []
    for key_len in (16, 1, 64):
        key = bytes((7 * i + 1) % 256 for i in range(key_len))
        for n in (0, 1, 12, 63, 64, 127, 128, 129, 255, 256, 300):
            data = _pattern(n)
            d = hashlib.blake2b(data, key=key, digest_size=32).digest()
            out.append(f"{key.hex()} {data.hex() or '-'} {d.hex()}")
    return "\n".join(out) + "\n"


def write() -> None:
    GOLDEN.mkdir(exist_ok=True)
    (GOLDEN / "layout.txt").write_text(layout_text())
    (GOLDEN / "blake2b.txt").write_text(blake2b_text())
    (GOLDEN / "documents.txt").write_text(documents_text())


if __name__ == "__main__":
    write()
