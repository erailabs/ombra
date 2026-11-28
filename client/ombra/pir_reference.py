# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Server side of private retrieval, as a pure-Python CPU reference on OpenFHE.

This is what later C++ and GPU code is checked against: same inputs (context, evaluation keys,
record column, query ciphertexts as bytes), and the answers must decrypt to the same bytes.
It lives in the client package, next to `ombra.pir` which it shares the layout with, because the
tests already import that package; it moves nowhere else until a server needs it.

It takes only public material: the serialized context, the serialized evaluation keys, the column
and the serialized queries. It never imports key generation or decryption, and a test checks the
source for that. The secret key cannot reach it.

Expansion: L levels of x -> x^(N/2^j + 1). The odd branch is multiplied by +x^(N-2^j) and then
negated, because a -1 would be encoded as t - 1 and cost about 20 bits of noise per level.
Answer per bucket: sum_b sel2_b * (sum_a sel1_a * pt[a, b]), the tensor products summed before one
relinearization, then Compress to one tower.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ombra.pir import (
    PirError,
    PirShape,
    bytes_to_coeffs,
    make_doc_shape,
    make_shape,
    parse_column,
    parse_doc_column,
    plane_chunk,
)


class PirServerReference:
    def __init__(
        self,
        context: bytes,
        mult_key: bytes,
        automorphism_key: bytes,
        column: bytes,
        m: int,
        seed: bytes,
    ) -> None:
        import openfhe as ofhe

        self._ofhe = ofhe
        self.cc = ofhe.DeserializeCryptoContextString(context, ofhe.BINARY)
        self._mult_key, self._auto_key = bytes(mult_key), bytes(automorphism_key)
        col = parse_column(column)
        self.shape: PirShape = make_shape(col.n_rows, m, col.row_size, seed)
        self._db = self._build_database(col.body)

    def _load_keys(self) -> None:
        """OpenFHE keeps evaluation keys in process-global caches. Empty them and load exactly the
        bytes this server was given, so nothing else is used (also when a client or another server
        lives in the same process, as in tests)."""
        ofhe = self._ofhe
        ofhe.ClearEvalMultKeys()
        ofhe.CryptoContext.ClearEvalAutomorphismKeys()
        ofhe.DeserializeEvalMultKeyString(self._mult_key, ofhe.BINARY)
        if self._auto_key:
            ofhe.DeserializeEvalAutomorphismKeyString(self._auto_key, ofhe.BINARY)

    # -- database ------------------------------------------------------------------------

    def _plain(self, values: Sequence[int]) -> Any:
        t = self.shape.pp.plain_modulus
        return self.cc.MakeCoefPackedPlaintext(
            [int(v) - t if v > t // 2 else int(v) for v in values]
        )

    def _build_bucket(self, body: bytes, rows: Sequence[int]) -> list[list[Any]]:
        """cells[a][b]: the plaintext of grid cell (a, b) of one bucket; missing cells are zero."""
        s = self.shape
        zero = self._plain([0] * s.pp.ring_dim)
        cells: list[Any] = []
        for p in range(s.r1 * s.r2):
            chunk = rows[p * s.rows_per_pt : (p + 1) * s.rows_per_pt]
            if chunk:
                data = b"".join(body[i * s.row_size : (i + 1) * s.row_size] for i in chunk)
                cells.append(self._plain(bytes_to_coeffs(data, s.pp)))
            else:
                cells.append(zero)
        return [[cells[a * s.r2 + b] for b in range(s.r2)] for a in range(s.r1)]

    def _build_database(self, body: bytes) -> list[list[list[Any]]]:
        """db[bucket][a][b]: the plaintext of grid cell (a, b); missing cells are zero."""
        return [self._build_bucket(body, rows) for rows in self.shape.contents]

    # -- expansion -----------------------------------------------------------------------

    def _expand(self, ct: Any, need: int) -> dict[int, Any]:
        """Selectors 0..need-1: selector p encrypts coefficient p of the query (times 2^L)."""
        cc, s = self.cc, self.shape
        n = s.pp.ring_dim
        keys = cc.GetEvalAutomorphismKeyMap(ct.GetKeyTag()) if s.levels else None
        nodes: dict[int, Any] = {0: ct}
        for j in range(s.levels):
            shift = [0] * n
            shift[n - (1 << j)] = 1  # +x^(N-2^j); negated below
            mono = self._plain(shift)
            nxt: dict[int, Any] = {}
            for r, c in nodes.items():
                tau = cc.EvalAutomorphism(c, n // (1 << j) + 1, keys)
                nxt[r] = cc.EvalAdd(c, tau)
                if r + (1 << j) < need:
                    nxt[r + (1 << j)] = cc.EvalNegate(cc.EvalMult(cc.EvalSub(c, tau), mono))
            nodes = {r: c for r, c in nxt.items() if r < need}
        return nodes

    # -- answer --------------------------------------------------------------------------

    def _answer_ct(self, c: int, sel: dict[int, Any], db: Any) -> list[bytes]:
        """The answers of the buckets of query ciphertext `c`, from its expanded selectors."""
        cc, ofhe, s = self.cc, self._ofhe, self.shape
        out: list[bytes] = []
        for slot in range(s.buckets_in_ct(c)):
            base = slot * s.coeffs_per_bucket
            cells = db[c * s.per_ct + slot]
            total = None
            for b in range(s.r2):
                inner = None
                for a in range(s.r1):
                    term = cc.EvalMult(sel[base + a], cells[a][b])
                    inner = term if inner is None else cc.EvalAdd(inner, term)
                prod = cc.EvalMultNoRelin(sel[base + s.r1 + b], inner)
                total = prod if total is None else cc.EvalAdd(total, prod)
            ans = cc.Compress(cc.Relinearize(total), 1)
            out.append(ofhe.Serialize(ans, ofhe.BINARY))
        return out

    def _expand_ct(self, c: int, raw: bytes) -> dict[int, Any]:
        s = self.shape
        ct = self._ofhe.DeserializeCiphertextString(raw, self._ofhe.BINARY)
        return self._expand(ct, s.buckets_in_ct(c) * s.coeffs_per_bucket)

    def answer(self, queries: Sequence[bytes]) -> list[bytes]:
        """One compressed answer per bucket, in bucket order."""
        s = self.shape
        if len(queries) != s.n_cts:
            raise PirError("wrong number of query ciphertexts")
        self._load_keys()
        out: list[bytes] = []
        for c, raw in enumerate(queries):
            out += self._answer_ct(c, self._expand_ct(c, raw), self._db)
        return out


class PirDocumentsReference(PirServerReference):
    """Server side of a document call over planes (see the planes spec in `ombra.pir`).

    The database is one document column (the blobs of one class in scope, in database order).
    Plane p is the database of chunk p of every row, with the shape, seed and cuckoo layout shared
    by all planes, so the selectors are expanded once and used for every plane. The planes are
    built and answered one at a time (a plane of a 256 KiB database is 14 times smaller than the
    whole). An empty column is answered over one padding row of zeros. Answers are plane-major.
    """

    def __init__(
        self,
        context: bytes,
        mult_key: bytes,
        automorphism_key: bytes,
        doc_column: bytes,
        m: int,
        seed: bytes,
    ) -> None:
        import openfhe as ofhe

        self._ofhe = ofhe
        self.cc = ofhe.DeserializeCryptoContextString(context, ofhe.BINARY)
        self._mult_key, self._auto_key = bytes(mult_key), bytes(automorphism_key)
        col = parse_doc_column(doc_column)
        self._rows = [col.row(i) for i in range(col.n_rows)] or [bytes(col.row_size)]
        self.doc = make_doc_shape(len(self._rows), m, col.row_size, seed)
        self.shape: PirShape = self.doc.shape

    def answer(self, queries: Sequence[bytes]) -> list[bytes]:
        """planes * buckets answers: plane 0 in bucket order, then plane 1, ..."""
        s, d = self.shape, self.doc
        if len(queries) != s.n_cts:
            raise PirError("wrong number of query ciphertexts")
        self._load_keys()
        sels = [self._expand_ct(c, raw) for c, raw in enumerate(queries)]
        out: list[bytes] = []
        for p in range(d.planes):
            body = b"".join(plane_chunk(r, p, d.class_size) for r in self._rows)
            for c, sel in enumerate(sels):
                # a bucket is built when its answer starts and freed after it: memory stays at
                # one bucket, not the whole plane
                out += self._answer_ct(c, sel, _LazyDb(self, body))
        return out


class _LazyDb:
    """db[bucket] built on access (one bucket at a time) from one plane's body."""

    def __init__(self, ref: PirServerReference, body: bytes) -> None:
        self._ref, self._body = ref, body

    def __getitem__(self, bucket: int) -> list[list[Any]]:
        return self._ref._build_bucket(self._body, self._ref.shape.contents[bucket])
