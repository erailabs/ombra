# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private record retrieval (PIR) over BFV: client side and the shared layout. CPU reference.

The client fetches the re-rank rows of its top candidates without the server learning which ones.
BFV on the preset `pir_bfv_n8192`, coefficient packing; the secret key never leaves the client.

What is here
  - the record row format (AES-256-GCM, one row per record) and the record column format (rows
    concatenated behind a version header), and how rows are laid out in plaintexts;
  - batched cuckoo hashing: m wanted rows go to 1.5 m buckets, each row stored in its 3 candidate
    buckets; the hash is keyed by a public per-database seed;
  - the fixed shape of a call (grid, bucket count, number of query ciphertexts), a function of
    (m, database size, row size, seed) only;
  - `PirClient`: key generation, query packing, decoding of answers.
The server side (expansion, answer) is `ombra.pir_reference`, which shares only this layout.

Protocol in short. A bucket's rows are packed into plaintexts, which form an r1 x r2 grid. The query
holds 2^-L mod t at coefficient a* and at r1 + b* of the bucket's slice of a ciphertext; many
buckets share one ciphertext. The server expands it into selectors (L levels of automorphisms),
and answers sum_b sel2_b * (sum_a sel1_a * pt[a, b]).
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import struct
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ombra import params as _params

PIR_PRESET = "pir_bfv_n8192"
HASHES = 3  # candidate buckets per row
BUCKET_FACTOR_NUM, BUCKET_FACTOR_DEN = 3, 2  # buckets = ceil(1.5 m)
SEED_LEN = 16

# Record column: magic | version | reserved (0) | row_size | n_rows, then the rows.
COLUMN_MAGIC = b"ORCL"
COLUMN_VERSION = 1
_COLUMN_HEAD = struct.Struct("<4sHHII")
COLUMN_HEADER_LEN = _COLUMN_HEAD.size  # 16

ROW_ALIGN = 64
NONCE_LEN = 12
TAG_LEN = 16
_ROW_DOMAIN = b"ombra.pir.row.v1"  # first bytes of the AAD; names the row format version


class PirError(ValueError):
    """A malformed row or column, a failed authentication, or an impossible shape."""


# ---------------------------------------------------------------- rows


def row_size_for(dim: int, n_ints: int) -> int:
    """Stored size of a row: 12-byte nonce, 4 dim + 8 n_ints bytes, 16-byte tag, padded to 64."""
    raw = NONCE_LEN + 4 * dim + 8 * n_ints + TAG_LEN
    return -(-raw // ROW_ALIGN) * ROW_ALIGN


def _aad(collection: str, cluster: int, record_id: bytes, version: int) -> bytes:
    c = collection.encode()
    return (
        _ROW_DOMAIN
        + struct.pack("<I", len(c))
        + c
        + struct.pack("<I", cluster)
        + struct.pack("<I", len(record_id))
        + bytes(record_id)
        + struct.pack("<I", version)
    )


class RowCodec:
    """Seal and open record rows: AES-256-GCM(float32[dim] || int64[n_ints]).

    The AES key is derived from the 32-byte master key (HKDF-SHA256, own domain), so rows and
    document blobs never share a key. The AAD binds (collection, cluster, id, version).
    """

    def __init__(self, master_key: bytes, collection: str, dim: int, n_ints: int) -> None:
        if len(master_key) != 32:
            raise PirError("master key must be 32 bytes")
        if dim < 1 or n_ints < 0:
            raise PirError("bad row dimensions")
        key = HKDF(hashes.SHA256(), 32, None, b"ombra pir row key v1").derive(bytes(master_key))
        self._aes = AESGCM(key)
        self._collection = collection
        self.dim, self.n_ints = dim, n_ints
        self.row_size = row_size_for(dim, n_ints)
        self._sealed_len = NONCE_LEN + 4 * dim + 8 * n_ints + TAG_LEN

    def __repr__(self) -> str:
        return "RowCodec(<redacted>)"

    def seal(
        self,
        cluster: int,
        record_id: bytes,
        version: int,
        vector: Sequence[float] | np.ndarray,
        ints: Sequence[int] | np.ndarray,
    ) -> bytes:
        vec = np.asarray(vector, dtype="<f4")
        ii = np.asarray(ints, dtype="<i8")
        if vec.shape != (self.dim,) or ii.shape != (self.n_ints,):
            raise PirError("vector or ints have the wrong length")
        nonce = os.urandom(NONCE_LEN)
        plain = vec.tobytes() + ii.tobytes()
        sealed = nonce + self._aes.encrypt(
            nonce, plain, _aad(self._collection, cluster, record_id, version)
        )
        return sealed + b"\0" * (self.row_size - len(sealed))

    def open(
        self, row: bytes, cluster: int, record_id: bytes, version: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (float32[dim], int64[n_ints]); raise PirError if the tag or AAD do not match."""
        if len(row) != self.row_size:
            raise PirError("row has the wrong size")
        if any(row[self._sealed_len :]):
            raise PirError("row padding is not zero")
        nonce = row[:NONCE_LEN]
        try:
            plain = self._aes.decrypt(
                nonce,
                row[NONCE_LEN : self._sealed_len],
                _aad(self._collection, cluster, record_id, version),
            )
        except Exception as e:  # cryptography.exceptions.InvalidTag
            raise PirError("row authentication failed") from e
        vec = np.frombuffer(plain[: 4 * self.dim], dtype="<f4").copy()
        ints = np.frombuffer(plain[4 * self.dim :], dtype="<i8").copy()
        return vec, ints


# ---------------------------------------------------------------- column


def pack_column(rows: Sequence[bytes], row_size: int) -> bytes:
    """A record column: the version header and the rows back to back."""
    if row_size <= 0 or row_size % ROW_ALIGN:
        raise PirError("row size must be a positive multiple of 64")
    for r in rows:
        if len(r) != row_size:
            raise PirError("row has the wrong size")
    return _COLUMN_HEAD.pack(COLUMN_MAGIC, COLUMN_VERSION, 0, row_size, len(rows)) + b"".join(rows)


@dataclass(frozen=True)
class Column:
    row_size: int
    n_rows: int
    body: bytes

    def row(self, i: int) -> bytes:
        if not 0 <= i < self.n_rows:
            raise IndexError(i)
        return self.body[i * self.row_size : (i + 1) * self.row_size]


def parse_column(data: bytes) -> Column:
    if len(data) < COLUMN_HEADER_LEN:
        raise PirError("column header is truncated")
    magic, version, reserved, row_size, n_rows = _COLUMN_HEAD.unpack_from(data)
    if magic != COLUMN_MAGIC:
        raise PirError("not a record column")
    if version != COLUMN_VERSION:
        raise PirError(f"unsupported column version {version}")
    if reserved != 0 or row_size == 0 or row_size % ROW_ALIGN:
        raise PirError("malformed column header")
    body = data[COLUMN_HEADER_LEN:]
    if len(body) != n_rows * row_size:
        raise PirError("column length does not match its header")
    return Column(row_size, n_rows, bytes(body))


# ------------------------------------------------- rows <-> plaintext coefficients


@dataclass(frozen=True)
class PirParams:
    """The numbers of the BFV preset the layout depends on."""

    ring_dim: int
    plain_modulus: int

    @property
    def coeff_bits(self) -> int:
        return self.plain_modulus.bit_length() - 1  # floor(log2 t)

    @property
    def plaintext_bytes(self) -> int:
        return self.ring_dim * self.coeff_bits // 8


def preset_params(name: str = PIR_PRESET) -> PirParams:
    p = _params.get_preset(name, scheme="BFV")
    return PirParams(int(p["ring_dim"]), int(p["plaintext_modulus"]))


def rows_per_plaintext(row_size: int, pp: PirParams) -> int:
    """Rows never span two plaintexts; a row bigger than one plaintext is refused."""
    n = pp.plaintext_bytes // row_size
    if n < 1:
        raise PirError("a row does not fit in one plaintext")
    return n


def bytes_to_coeffs(data: bytes, pp: PirParams) -> np.ndarray:
    """Little-endian bit stream, coeff_bits per coefficient, zero padded: int64[ring_dim]."""
    if len(data) > pp.plaintext_bytes:
        raise PirError("data does not fit in one plaintext")
    buf = np.frombuffer(data.ljust(pp.plaintext_bytes, b"\0"), dtype=np.uint8)
    bits = np.unpackbits(buf, bitorder="little").reshape(pp.ring_dim, pp.coeff_bits)
    return bits.astype(np.int64) @ (1 << np.arange(pp.coeff_bits, dtype=np.int64))


def coeffs_to_bytes(coeffs: Sequence[int] | np.ndarray, pp: PirParams) -> bytes:
    c = np.asarray(coeffs, dtype=np.int64) % pp.plain_modulus
    if c.shape != (pp.ring_dim,) or int(c.max()) >> pp.coeff_bits:
        raise PirError("decrypted coefficients are not a stored plaintext")
    bits = ((c[:, None] >> np.arange(pp.coeff_bits, dtype=np.int64)) & 1).astype(np.uint8)
    return np.packbits(bits.reshape(-1), bitorder="little").tobytes()


# ---------------------------------------------------------------- hashing


def new_seed() -> bytes:
    return os.urandom(SEED_LEN)


def n_buckets(m: int) -> int:
    return max(HASHES, -(-m * BUCKET_FACTOR_NUM // BUCKET_FACTOR_DEN))


def candidate_buckets(seed: bytes, row: int, buckets: int) -> tuple[int, ...]:
    """The 3 distinct candidate buckets of a row, from a hash keyed by the public seed."""
    if buckets < HASHES:
        raise PirError("too few buckets")
    out: list[int] = []
    ctr = 0
    while len(out) < HASHES:
        digest = hashlib.blake2b(struct.pack("<QI", row, ctr), key=seed, digest_size=32).digest()
        for w in struct.unpack("<8I", digest):
            b = w % buckets
            if b not in out:
                out.append(b)
                if len(out) == HASHES:
                    break
        ctr += 1
    return tuple(out)


def bucket_contents(seed: bytes, n_rows: int, buckets: int) -> list[list[int]]:
    """Row indices of each bucket, ascending. Every row is in 3 buckets."""
    contents: list[list[int]] = [[] for _ in range(buckets)]
    for i in range(n_rows):
        for b in candidate_buckets(seed, i, buckets):
            contents[b].append(i)
    return contents


# ---------------------------------------------------------------- shape


@dataclass(frozen=True)
class PirShape:
    """Everything the sizes of a call depend on: (m, database size, row size, public seed)."""

    n_rows: int
    m: int
    row_size: int
    seed: bytes
    pp: PirParams
    buckets: int
    rows_per_pt: int
    bucket_rows: int  # rows of the fullest bucket; every bucket is padded to this
    pts_per_bucket: int
    r1: int
    r2: int
    per_ct: int  # buckets sharing one query ciphertext
    n_cts: int
    levels: int  # L: expansion levels, 2^L >= coefficients in use per ciphertext
    _contents: list[list[int]] = field(repr=False, compare=False, default_factory=list)

    @property
    def contents(self) -> list[list[int]]:
        return self._contents

    @property
    def coeffs_per_bucket(self) -> int:
        return self.r1 + self.r2

    def eval_automorphism_indices(self) -> list[int]:
        return [self.pp.ring_dim // (1 << j) + 1 for j in range(self.levels)]

    def buckets_in_ct(self, c: int) -> int:
        return min(self.per_ct, self.buckets - c * self.per_ct)


def make_shape(
    n_rows: int, m: int, row_size: int, seed: bytes, pp: PirParams | None = None
) -> PirShape:
    pp = pp or preset_params()
    if n_rows < 1 or m < 1:
        raise PirError("need at least one row and m >= 1")
    if len(seed) != SEED_LEN:
        raise PirError("seed must be 16 bytes")
    rpp = rows_per_plaintext(row_size, pp)
    buckets = n_buckets(m)
    contents = bucket_contents(seed, n_rows, buckets)
    bucket_rows = max(1, max(len(c) for c in contents))
    pts = -(-bucket_rows // rpp)
    r1 = math.isqrt(pts - 1) + 1  # ceil(sqrt(pts))
    r2 = -(-pts // r1)
    width = r1 + r2
    per_ct = min(buckets, pp.ring_dim // width)
    if per_ct < 1:
        raise PirError("grid too large for one ciphertext")
    n_cts = -(-buckets // per_ct)
    levels = max(0, (per_ct * width - 1).bit_length())
    return PirShape(
        n_rows, m, row_size, bytes(seed), pp, buckets, rpp, bucket_rows, pts, r1, r2, per_ct,
        n_cts, levels, contents,
    )  # fmt: skip


# ---------------------------------------------------------------- batch planning


@dataclass
class BatchPlan:
    """Which row each bucket asks for. None: a dummy query. Never sent to the server."""

    shape: PirShape
    assignment: list[int | None]
    targets: list[int]  # rows that will be retrieved, in rank order
    dropped: list[int]  # candidates lost to cuckoo failure (counted, never retried)
    filled: list[int]  # random real rows added to reach m
    dummy_cells: dict[int, tuple[int, int]] = field(default_factory=dict)  # bucket -> (a, b)

    def cell(self, bucket: int) -> tuple[int, int]:
        row = self.assignment[bucket]
        if row is None:
            return self.dummy_cells[bucket]
        rank = self.shape.contents[bucket].index(row)
        return divmod(rank // self.shape.rows_per_pt, self.shape.r2)


def _cuckoo(cands: dict[int, tuple[int, ...]], order: list[int], buckets: int):
    """Assign rows (in rank order) to distinct buckets by augmenting paths; a row with no
    augmenting path is dropped, so the highest-ranked rows win."""
    owner: list[int | None] = [None] * buckets
    placed: list[int] = []
    dropped: list[int] = []
    for row in order:
        parent: dict[int, tuple[int | None, int]] = {}
        queue: deque[tuple[int, int | None]] = deque([(row, None)])
        done = False
        while queue and not done:
            r, held = queue.popleft()
            for b in cands[r]:
                if b in parent:
                    continue
                parent[b] = (held, r)
                if owner[b] is None:
                    cur: int | None = b
                    while cur is not None:
                        prev, rr = parent[cur]
                        owner[cur] = rr
                        cur = prev
                    done = True
                    break
                queue.append((owner[b], b))  # type: ignore[arg-type]
        (placed if done else dropped).append(row)
    return owner, placed, dropped


def plan_batch(
    shape: PirShape, candidates: Sequence[int], rng: random.Random | None = None
) -> BatchPlan:
    """Fill the candidates (best first) to m with random real rows, then place them.

    More than m candidates: the lowest-ranked are cut. A cuckoo failure drops the lowest-ranked
    row that does not fit and counts it; the call is never repeated.
    """
    rng = rng or random.SystemRandom()
    seen: set[int] = set()
    ranked: list[int] = []
    for c in candidates:
        if not 0 <= c < shape.n_rows:
            raise PirError("candidate out of range")
        if c not in seen:
            seen.add(c)
            ranked.append(c)
    ranked = ranked[: shape.m]
    filled: list[int] = []
    want = min(shape.m, shape.n_rows)
    while len(ranked) < want:
        r = rng.randrange(shape.n_rows)
        if r not in seen:
            seen.add(r)
            ranked.append(r)
            filled.append(r)
    cands = {r: candidate_buckets(shape.seed, r, shape.buckets) for r in ranked}
    owner, placed, dropped = _cuckoo(cands, ranked, shape.buckets)
    plan = BatchPlan(shape, owner, placed, dropped, filled)
    for b in range(shape.buckets):
        if owner[b] is None:
            plan.dummy_cells[b] = (rng.randrange(shape.r1), rng.randrange(shape.r2))
    return plan


# ---------------------------------------------------------------- client


@dataclass(frozen=True)
class EvalKeys:
    """Public evaluation keys, serialized: sent once per client."""

    mult: bytes
    automorphism: bytes

    def __len__(self) -> int:
        return len(self.mult) + len(self.automorphism)


class PirClient:
    """Holds the secret key. Produces queries and decodes answers; never talks to a server."""

    def __init__(self, shape: PirShape, cc: Any | None = None, keys: Any | None = None) -> None:
        import openfhe as ofhe

        self._ofhe = ofhe
        self.shape = shape
        if cc is None:
            _, cc = _params.make_bfv_context(PIR_PRESET)
        self.cc = cc
        self._keys = keys or cc.KeyGen()

    def __repr__(self) -> str:
        return "PirClient(<redacted>)"

    def context_bytes(self) -> bytes:
        return self._ofhe.Serialize(self.cc, self._ofhe.BINARY)

    def eval_keys(self) -> EvalKeys:
        """Relinearization key and automorphism keys for exactly the indices the shape needs."""
        ofhe = self._ofhe
        self.cc.EvalMultKeyGen(self._keys.secretKey)
        idx = self.shape.eval_automorphism_indices()
        if idx:
            self.cc.EvalAutomorphismKeyGen(self._keys.secretKey, idx)
        tag = self._keys.publicKey.GetKeyTag()
        return EvalKeys(
            ofhe.SerializeEvalMultKeyString(ofhe.BINARY, tag),
            ofhe.SerializeEvalAutomorphismKeyString(ofhe.BINARY, tag) if idx else b"",
        )

    def _plain(self, values: Sequence[int]) -> Any:
        t = self.shape.pp.plain_modulus
        return self.cc.MakeCoefPackedPlaintext([v - t if v > t // 2 else v for v in values])

    def build_queries(self, plan: BatchPlan) -> list[bytes]:
        """One ciphertext per group of buckets: 2^-L mod t at a* and r1 + b* of each slice."""
        s = self.shape
        t, n = s.pp.plain_modulus, s.pp.ring_dim
        inv = pow(2, -s.levels, t)
        out = []
        for c in range(s.n_cts):
            coef = [0] * n
            for slot in range(s.buckets_in_ct(c)):
                a, b = plan.cell(c * s.per_ct + slot)
                base = slot * s.coeffs_per_bucket
                coef[base + a] = inv
                coef[base + s.r1 + b] = inv
            ct = self.cc.Encrypt(self._keys.publicKey, self._plain(coef))
            out.append(self._ofhe.Serialize(ct, self._ofhe.BINARY))
        return out

    def decode_answers(self, plan: BatchPlan, answers: Sequence[bytes]) -> dict[int, bytes]:
        """Row bytes of every retrieved row, by row index. Dummy buckets are not decrypted."""
        s = self.shape
        if len(answers) != s.buckets:
            raise PirError("wrong number of answers")
        rows: dict[int, bytes] = {}
        for b, row in enumerate(plan.assignment):
            if row is None:
                continue
            ct = self._ofhe.DeserializeCiphertextString(answers[b], self._ofhe.BINARY)
            pt = self.cc.Decrypt(ct, self._keys.secretKey)
            pt.SetLength(s.pp.ring_dim)
            data = coeffs_to_bytes(pt.GetCoefPackedValue(), s.pp)
            slot = s.contents[b].index(row) % s.rows_per_pt
            rows[row] = data[slot * s.row_size : (slot + 1) * s.row_size]
        return rows

    def noise_budget(self, answer: bytes, expected: Sequence[int] | np.ndarray) -> int:
        """Bits of noise margin of an answer whose plaintext is known: the number of times the
        ciphertext can be doubled before it no longer decrypts exactly (-1 if it is wrong already).
        A single-trial lower bound; for tests and measurements, never used on real data."""
        cc, ofhe, t = self.cc, self._ofhe, self.shape.pp.plain_modulus
        want = np.asarray(expected, dtype=np.int64) % t
        ct = ofhe.DeserializeCiphertextString(answer, ofhe.BINARY)
        k = 0
        while k < 40:
            pt = cc.Decrypt(ct, self._keys.secretKey)
            pt.SetLength(self.shape.pp.ring_dim)
            got = np.asarray(pt.GetCoefPackedValue(), dtype=np.int64) % t
            if not np.array_equal(got, (want * pow(2, k, t)) % t):
                break
            ct = cc.EvalAdd(ct, ct)
            k += 1
        return k - 1
