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
import json
import math
import os
import random
import struct
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, NamedTuple

import numpy as np
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ombra import params as _params
from ombra.file_format import Kind, write_envelope

PIR_PRESET = "pir_bfv_n8192"
HASHES = 3  # candidate buckets per row
BUCKET_FACTOR_NUM, BUCKET_FACTOR_DEN = 3, 2  # buckets = ceil(1.5 m)
SEED_LEN = 16

# Record column: magic | version | reserved (0) | row_size | n_rows, then the rows.
# Version 1 holds v1 rows (vector and integers); version 2 holds v2 rows (also metadata and the
# document pointer). Only the rows differ: the header and the layout of the column are the same,
# and the server never opens a row. Readers accept both versions.
COLUMN_MAGIC = b"ORCL"
COLUMN_VERSION = 2
COLUMN_VERSIONS = (1, 2)
_COLUMN_HEAD = struct.Struct("<4sHHII")
COLUMN_HEADER_LEN = _COLUMN_HEAD.size  # 16

# Document column: magic | version | document class | row_size | n_rows, then the rows. Same
# layout as the record column; the u16 that is "reserved" there holds the class (1 or 2) here.
# Its rows are the stored document blobs of that class, byte for byte (row_size = class size).
DOC_COLUMN_MAGIC = b"ODCL"
DOC_COLUMN_VERSION = 1
DOC_CLASS_NONE, DOC_CLASS_16K, DOC_CLASS_256K, DOC_CLASS_LARGE = 0, 1, 2, 3
DOC_CLASS_SIZES = {DOC_CLASS_16K: 16384, DOC_CLASS_256K: 262144}  # the classes fetched privately
PRACTICAL_CLASSES = tuple(DOC_CLASS_SIZES)

ROW_ALIGN = 64
NONCE_LEN = 12
TAG_LEN = 16
_ROW_DOMAIN_V1 = b"ombra.pir.row.v1"  # first bytes of the AAD; names the row format version
_ROW_DOMAIN_V2 = b"ombra.pir.row.v2"
_ROW_DOMAIN = _ROW_DOMAIN_V1  # kept for older callers
MAX_ROW_BYTES = 19456  # one plaintext of the preset: a row never spans two
_V2_FIXED = 2 + 1 + 4  # u16 metadata length, u8 document class, u32 document index
DEFAULT_METADATA_BYTES = 512


class PirError(ValueError):
    """A malformed row or column, a failed authentication, or an impossible shape."""


# ---------------------------------------------------------------- rows


def row_size_for(dim: int, n_ints: int, metadata_bytes: int | None = None) -> int:
    """Stored size of a row: 12-byte nonce, the plaintext, 16-byte tag, padded to 64.

    v1 (`metadata_bytes` None): the plaintext is 4 dim + 8 n_ints bytes. v2: it also holds
    u16 metadata length, `metadata_bytes` of padded JSON, u8 document class, u32 document index.
    """
    plain = 4 * dim + 8 * n_ints
    if metadata_bytes is not None:
        plain += _V2_FIXED + metadata_bytes
    raw = NONCE_LEN + plain + TAG_LEN
    return -(-raw // ROW_ALIGN) * ROW_ALIGN


def max_metadata_bytes(dim: int, n_ints: int, pp: PirParams | None = None) -> int:
    """The largest metadata slot whose v2 row still fits one plaintext, rounded down to 64.

    Exactly: nonce 12 + 4 dim + 8 n_ints + 7 fixed + M + tag 16 <= plaintext bytes (19,456 on the
    preset), so M <= 19,421 - 4 dim - 8 n_ints; then M is rounded down to a multiple of 64. At 768
    dimensions and no integers that is 16,320. A negative room (a row does not fit) is a PirError.
    """
    limit = (pp or preset_params()).plaintext_bytes
    room = limit - (NONCE_LEN + 4 * dim + 8 * n_ints + _V2_FIXED + TAG_LEN)
    if room < 0:
        raise PirError("a row does not fit in one plaintext")
    return room // ROW_ALIGN * ROW_ALIGN


def metadata_json(metadata: dict[str, Any] | None) -> bytes:
    """The compact, key-sorted JSON of a metadata dict, as a v2 row stores it."""
    return json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True).encode()


def _aad(
    collection: str, cluster: int, record_id: bytes, version: int, domain: bytes = _ROW_DOMAIN_V1
) -> bytes:
    c = collection.encode()
    return (
        domain
        + struct.pack("<I", len(c))
        + c
        + struct.pack("<I", cluster)
        + struct.pack("<I", len(record_id))
        + bytes(record_id)
        + struct.pack("<I", version)
    )


class OpenedRow(NamedTuple):
    """What a row holds. A v1 row has empty metadata and no document (class 0)."""

    vector: np.ndarray
    ints: np.ndarray
    metadata: dict[str, Any]
    doc_class: int
    doc_index: int


class RowCodec:
    """Seal and open record rows: AES-256-GCM of the row plaintext.

    v1 (`metadata_bytes` None): float32[dim] || int64[n_ints].
    v2 (`metadata_bytes` = M): float32[dim] || int64[n_ints] || u16 LE metadata length ||
    compact metadata JSON zero padded to M || u8 document class || u32 LE document index. The
    class is 0 (none), 1 (16 KiB), 2 (256 KiB) or 3 (large, read by id); the index is the
    document's row in its block's column of that class (0 for classes 0 and 3).

    The AES key is derived from the 32-byte master key (HKDF-SHA256, own domain), so rows and
    document blobs never share a key. The AAD binds (domain, collection, cluster, id, version);
    the domain `ombra.pir.row.v1` or `.v2` names the row format.
    """

    def __init__(
        self,
        master_key: bytes,
        collection: str,
        dim: int,
        n_ints: int,
        metadata_bytes: int | None = None,
    ) -> None:
        if len(master_key) != 32:
            raise PirError("master key must be 32 bytes")
        if dim < 1 or n_ints < 0:
            raise PirError("bad row dimensions")
        key = HKDF(hashes.SHA256(), 32, None, b"ombra pir row key v1").derive(bytes(master_key))
        self._aes = AESGCM(key)
        self._collection = collection
        self.dim, self.n_ints = dim, n_ints
        self.metadata_bytes = metadata_bytes
        self.format = 1 if metadata_bytes is None else 2
        self._domain = _ROW_DOMAIN_V1 if metadata_bytes is None else _ROW_DOMAIN_V2
        self._plain_len = 4 * dim + 8 * n_ints
        if metadata_bytes is not None:
            if metadata_bytes < 0 or metadata_bytes > max_metadata_bytes(dim, n_ints):
                raise PirError("metadata_bytes does not fit a row in one plaintext")
            self._plain_len += _V2_FIXED + metadata_bytes
        self.row_size = row_size_for(dim, n_ints, metadata_bytes)
        self._sealed_len = NONCE_LEN + self._plain_len + TAG_LEN

    def __repr__(self) -> str:
        return "RowCodec(<redacted>)"

    def seal(
        self,
        cluster: int,
        record_id: bytes,
        version: int,
        vector: Sequence[float] | np.ndarray,
        ints: Sequence[int] | np.ndarray,
        metadata: dict[str, Any] | None = None,
        doc_class: int = DOC_CLASS_NONE,
        doc_index: int = 0,
    ) -> bytes:
        vec = np.asarray(vector, dtype="<f4")
        ii = np.asarray(ints, dtype="<i8")
        if vec.shape != (self.dim,) or ii.shape != (self.n_ints,):
            raise PirError("vector or ints have the wrong length")
        plain = vec.tobytes() + ii.tobytes()
        if self.metadata_bytes is None:
            if metadata or doc_class or doc_index:
                raise PirError("a v1 row holds no metadata or document pointer")
        else:
            meta = metadata_json(metadata)
            if len(meta) > self.metadata_bytes:
                raise PirError(
                    f"metadata is {len(meta)} bytes as JSON, above this collection's "
                    f"limit of {self.metadata_bytes} bytes"
                )
            if doc_class not in (0, 1, 2, 3) or not 0 <= doc_index < 1 << 32:
                raise PirError("bad document pointer")
            if doc_class in (DOC_CLASS_NONE, DOC_CLASS_LARGE) and doc_index:
                raise PirError("document index must be 0 for classes 0 and 3")
            plain += (
                struct.pack("<H", len(meta))
                + meta.ljust(self.metadata_bytes, b"\0")
                + struct.pack("<BI", doc_class, doc_index)
            )
        nonce = os.urandom(NONCE_LEN)
        aad = _aad(self._collection, cluster, record_id, version, self._domain)
        sealed = nonce + self._aes.encrypt(nonce, plain, aad)
        return sealed + b"\0" * (self.row_size - len(sealed))

    def open_row(self, row: bytes, cluster: int, record_id: bytes, version: int) -> OpenedRow:
        """Open a row of this codec's format; raise PirError if the tag or AAD do not match."""
        if len(row) != self.row_size:
            raise PirError("row has the wrong size")
        if any(row[self._sealed_len :]):
            raise PirError("row padding is not zero")
        nonce = row[:NONCE_LEN]
        try:
            plain = self._aes.decrypt(
                nonce,
                row[NONCE_LEN : self._sealed_len],
                _aad(self._collection, cluster, record_id, version, self._domain),
            )
        except Exception as e:  # cryptography.exceptions.InvalidTag
            raise PirError("row authentication failed") from e
        vec = np.frombuffer(plain[: 4 * self.dim], dtype="<f4").copy()
        end = 4 * self.dim + 8 * self.n_ints
        ints = np.frombuffer(plain[4 * self.dim : end], dtype="<i8").copy()
        if self.metadata_bytes is None:
            return OpenedRow(vec, ints, {}, DOC_CLASS_NONE, 0)
        (n,) = struct.unpack_from("<H", plain, end)
        body = plain[end + 2 : end + 2 + self.metadata_bytes]
        doc_class, doc_index = struct.unpack_from("<BI", plain, end + 2 + self.metadata_bytes)
        if n > self.metadata_bytes or any(body[n:]) or doc_class > DOC_CLASS_LARGE:
            raise PirError("row is malformed")
        try:
            meta = json.loads(body[:n])
        except ValueError as e:
            raise PirError("row metadata is not JSON") from e
        if not isinstance(meta, dict):
            raise PirError("row metadata is not an object")
        return OpenedRow(vec, ints, meta, doc_class, doc_index)

    def open(
        self, row: bytes, cluster: int, record_id: bytes, version: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (float32[dim], int64[n_ints]); raise PirError if the tag or AAD do not match."""
        r = self.open_row(row, cluster, record_id, version)
        return r.vector, r.ints


# ---------------------------------------------------------------- column


def pack_column(rows: Sequence[bytes], row_size: int, version: int = COLUMN_VERSION) -> bytes:
    """A record column: the version header and the rows back to back. `version` 1 is for v1
    rows (an old collection), 2 for v2 rows."""
    if version not in COLUMN_VERSIONS:
        raise PirError(f"unsupported column version {version}")
    if row_size <= 0 or row_size % ROW_ALIGN:
        raise PirError("row size must be a positive multiple of 64")
    for r in rows:
        if len(r) != row_size:
            raise PirError("row has the wrong size")
    return _COLUMN_HEAD.pack(COLUMN_MAGIC, version, 0, row_size, len(rows)) + b"".join(rows)


@dataclass(frozen=True)
class Column:
    row_size: int
    n_rows: int
    body: bytes
    version: int = COLUMN_VERSION
    doc_class: int = 0  # document columns only

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
    if version not in COLUMN_VERSIONS:
        raise PirError(f"unsupported column version {version}")
    if reserved != 0 or row_size == 0 or row_size % ROW_ALIGN:
        raise PirError("malformed column header")
    body = data[COLUMN_HEADER_LEN:]
    if len(body) != n_rows * row_size:
        raise PirError("column length does not match its header")
    return Column(row_size, n_rows, bytes(body), version)


def doc_class_of(blob_len: int | None) -> int:
    """The document class of a stored blob by its size: 1 (16 KiB), 2 (256 KiB), 3 (larger), and
    0 for no document."""
    if blob_len is None:
        return DOC_CLASS_NONE
    for cls, size in DOC_CLASS_SIZES.items():
        if blob_len == size:
            return cls
    return DOC_CLASS_LARGE


def pack_doc_column(rows: Sequence[bytes], doc_class: int) -> bytes:
    """A document column of one class: the stored document blobs of the block's vectors in that
    class, in block order, behind a header that names the class."""
    size = DOC_CLASS_SIZES.get(doc_class)
    if size is None:
        raise PirError("not a document class that is fetched privately")
    for r in rows:
        if len(r) != size:
            raise PirError("document blob has the wrong size for its class")
    head = _COLUMN_HEAD.pack(DOC_COLUMN_MAGIC, DOC_COLUMN_VERSION, doc_class, size, len(rows))
    return head + b"".join(rows)


def parse_doc_column(data: bytes) -> Column:
    if len(data) < COLUMN_HEADER_LEN:
        raise PirError("column header is truncated")
    magic, version, doc_class, row_size, n_rows = _COLUMN_HEAD.unpack_from(data)
    if magic != DOC_COLUMN_MAGIC:
        raise PirError("not a document column")
    if version != DOC_COLUMN_VERSION:
        raise PirError(f"unsupported document column version {version}")
    if DOC_CLASS_SIZES.get(doc_class) != row_size:
        raise PirError("malformed document column header")
    body = data[COLUMN_HEADER_LEN:]
    if len(body) != n_rows * row_size:
        raise PirError("column length does not match its header")
    return Column(row_size, n_rows, bytes(body), version, doc_class)


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


# ---------------------------------------------------------------- planes (documents)
#
# Spec (portable; the C++ server must produce exactly this). A document class has a size S
# (16,384 or 262,144 bytes) and a database of n rows of S bytes each: the stored document blobs of
# the class, in database order. With P = plaintext_bytes of the preset (19,456):
#   planes   c = ceil(S / P)                        (1 for 16 KiB, 14 for 256 KiB)
#   chunk p of a row  = row[p * P : (p + 1) * P], the last one zero padded to P bytes when c > 1;
#                       for c = 1 the chunk is the whole row, S bytes
#   plane row size    = min(S, P)                   (a multiple of 64)
# Plane p is an ordinary PIR database of the n chunks p, one per row, in the same order: the same
# shape (make_shape with row size = plane row size), the same hash seed and the same cuckoo
# layout as every other plane, so one set of query ciphertexts serves all planes. A scope with no
# document of the class has n = 0 rows; the server then answers over one padding row of zeros, so
# the shape of the call (and so every size) does not tell that the scope is empty.
# The answer is plane-major: the `buckets` answers of plane 0 in bucket order, then plane 1, ...
# (planes * buckets answers). The client decodes plane p of its row r from the bucket the plan
# assigned to r, concatenates the c chunks and cuts the result to S bytes.


def plane_layout(class_size: int, pp: PirParams | None = None) -> tuple[int, int]:
    """(planes, plane row size) of a document class of `class_size` bytes."""
    pp = pp or preset_params()
    if class_size <= 0 or class_size % ROW_ALIGN:
        raise PirError("class size must be a positive multiple of 64")
    return -(-class_size // pp.plaintext_bytes), min(class_size, pp.plaintext_bytes)


@dataclass(frozen=True)
class DocShape:
    """The shape of one document call: one PIR shape shared by `planes` planes."""

    shape: PirShape
    class_size: int
    planes: int

    @property
    def n_answers(self) -> int:
        return self.planes * self.shape.buckets


def make_doc_shape(
    n_rows: int, m: int, class_size: int, seed: bytes, pp: PirParams | None = None
) -> DocShape:
    pp = pp or preset_params()
    planes, plane_row = plane_layout(class_size, pp)
    return DocShape(make_shape(max(1, n_rows), m, plane_row, seed, pp), class_size, planes)


def plane_chunk(row: bytes, plane: int, class_size: int, pp: PirParams | None = None) -> bytes:
    """Chunk `plane` of a row of `class_size` bytes, zero padded to the plane row size."""
    pp = pp or preset_params()
    planes, plane_row = plane_layout(class_size, pp)
    if len(row) != class_size or not 0 <= plane < planes:
        raise PirError("row or plane out of range")
    return row[plane * plane_row : (plane + 1) * plane_row].ljust(plane_row, b"\0")


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


ALL_LEVELS = 13  # automorphism levels of a full key set: N / 2^j + 1 for j < 13 (N = 8192)
_KEY_MAGIC = b"OPKB"
_KEY_VERSION = 1
_KEY_HEAD = struct.Struct("<4sHI")


def all_automorphism_indices(pp: PirParams | None = None) -> list[int]:
    """The automorphism indices of a complete key set, which serves a shape of any size."""
    n = (pp or preset_params()).ring_dim
    return [n // (1 << j) + 1 for j in range(ALL_LEVELS)]


class PirClient:
    """Holds the secret key. Produces queries and decodes answers; never talks to a server.

    The key pair does not depend on a shape: one pair serves every call, and its evaluation keys
    cover all 13 expansion levels. A client made without a shape can generate keys, save and
    restore them (`secret_blob`, `restore`) and build key files; `with_shape` gives the view of
    the same keys for one call.
    """

    def __init__(
        self, shape: PirShape | None = None, cc: Any | None = None, keys: Any | None = None
    ) -> None:
        import openfhe as ofhe

        self._ofhe = ofhe
        self.shape = shape  # type: ignore[assignment]
        if cc is None:
            _, cc = _params.make_bfv_context(PIR_PRESET)
        self.cc = cc
        self._keys = keys or cc.KeyGen()

    @classmethod
    def restore(cls, blob: bytes) -> PirClient:
        """The client of a key pair saved with `secret_blob` (on a fresh context of the preset)."""
        import openfhe as ofhe

        if len(blob) < _KEY_HEAD.size:
            raise PirError("key blob is truncated")
        magic, version, n = _KEY_HEAD.unpack_from(blob)
        if magic != _KEY_MAGIC or version != _KEY_VERSION:
            raise PirError("key blob has an unknown format or version")
        sk_raw, pk_raw = blob[_KEY_HEAD.size : _KEY_HEAD.size + n], blob[_KEY_HEAD.size + n :]
        try:
            keys = SimpleNamespace(
                secretKey=ofhe.DeserializePrivateKeyString(sk_raw, ofhe.BINARY),
                publicKey=ofhe.DeserializePublicKeyString(pk_raw, ofhe.BINARY),
            )
        except Exception as exc:
            raise PirError("key could not be deserialized") from exc
        if keys.secretKey.GetKeyTag() != keys.publicKey.GetKeyTag():
            raise PirError("the secret key and the public key do not match")
        return cls(None, None, keys)

    def secret_blob(self) -> bytes:
        """The key pair as bytes, in memory only: for the passphrase-protected key file."""
        ofhe = self._ofhe
        sk = ofhe.Serialize(self._keys.secretKey, ofhe.BINARY)
        pk = ofhe.Serialize(self._keys.publicKey, ofhe.BINARY)
        return _KEY_HEAD.pack(_KEY_MAGIC, _KEY_VERSION, len(sk)) + sk + pk

    @property
    def key_tag(self) -> str:
        """Tag of the public key; public (the server compares it with its stored keys)."""
        return str(self._keys.publicKey.GetKeyTag())

    def with_shape(self, shape: PirShape) -> PirClient:
        """The same keys, for the queries and answers of one call."""
        return PirClient(shape, self.cc, self._keys)

    def __repr__(self) -> str:
        return "PirClient(<redacted>)"

    def context_bytes(self) -> bytes:
        return self._ofhe.Serialize(self.cc, self._ofhe.BINARY)

    def eval_keys(self, *, all_levels: bool = False) -> EvalKeys:
        """Relinearization key and automorphism keys: for exactly the indices the shape needs, or
        (`all_levels`, and always without a shape) for all 13 levels, which serve any shape."""
        ofhe = self._ofhe
        full = all_levels or self.shape is None
        if full and getattr(self, "_full_keys", None) is not None:
            return self._full_keys  # type: ignore[no-any-return]
        self.cc.EvalMultKeyGen(self._keys.secretKey)
        idx = all_automorphism_indices() if full else self.shape.eval_automorphism_indices()
        if idx:
            self.cc.EvalAutomorphismKeyGen(self._keys.secretKey, idx)
        tag = self._keys.publicKey.GetKeyTag()
        out = EvalKeys(
            ofhe.SerializeEvalMultKeyString(ofhe.BINARY, tag),
            ofhe.SerializeEvalAutomorphismKeyString(ofhe.BINARY, tag) if idx else b"",
        )
        if full:
            self._full_keys = out
        return out

    def key_files(self, openfhe_version: str) -> dict[str, bytes]:
        """The three public key files (all 13 levels), keyed by their server kind names.

        Each is a file envelope (the same format and version as the search keys) with the preset
        `pir_bfv_n8192`: the context is a context file, the other two are public evaluation keys.
        They go in one UploadKeys stream (KEY_KIND_PIR_CONTEXT, _EVAL_MULT, _AUTOMORPHISM), apart
        from the search keys. Only public material; the secret key stays in this object.
        """
        keys = self.eval_keys(all_levels=True)
        return {
            "pir_context": write_envelope(
                Kind.CONTEXT, PIR_PRESET, openfhe_version, self.context_bytes()
            ),
            "pir_eval_mult": write_envelope(
                Kind.PUBLIC_EVAL_KEY, PIR_PRESET, openfhe_version, keys.mult
            ),
            "pir_automorphism": write_envelope(
                Kind.PUBLIC_EVAL_KEY, PIR_PRESET, openfhe_version, keys.automorphism
            ),
        }

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

    def decode_document_answers(
        self, plan: BatchPlan, answers: Sequence[bytes], doc: DocShape
    ) -> dict[int, bytes]:
        """The stored blob of every retrieved row (class_size bytes), by row index. `answers` are
        plane-major: planes * buckets answers (see the planes spec above)."""
        b = self.shape.buckets
        if doc.shape is not self.shape and doc.shape != self.shape:
            raise PirError("shape mismatch")
        if len(answers) != doc.planes * b:
            raise PirError("wrong number of answers")
        parts: dict[int, list[bytes]] = {}
        for p in range(doc.planes):
            for row, chunk in self.decode_answers(plan, answers[p * b : (p + 1) * b]).items():
                parts.setdefault(row, []).append(chunk)
        return {row: b"".join(c)[: doc.class_size] for row, c in parts.items()}

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
