# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private record retrieval in the client library: record columns written at insert time, and the
RetrieveRecords call that fetches the re-rank rows of a search without the server learning which.

A collection made with `private_retrieval=True` stores, next to every block of vectors, a record
column: one sealed row per slot (`ombra.pir.RowCodec`, AES-256-GCM) with the exact float32 vector
and the int64 values the re-rank needs. A search then re-ranks with one RetrieveRecords call
instead of one GetBlob per candidate. The server sees the scope (the clusters the search requested,
decoys included, or all of them at Strict), the number m of rows and the sizes; not which rows.

Rows hold: the float32 vector; on a collection with range fields also the filter values of every
field (equality values and range buckets) and the raw integer of every range field, in schema
order. A collection without range fields has no integers in its rows (the band is exact).

Row AAD binds (collection base name, cluster, id, `ROW_VERSION`). A row that does not open (moved,
stale, corrupt) is dropped and counted, never trusted.

Row v2 (collections made from this version on) also holds the vector's metadata (a fixed slot of
`metadata_bytes`, chosen at create and stored in the marker) and a pointer to its document: the
document's class (16 KiB, 256 KiB, large) and its index in its block's document column of that
class. So a private search returns metadata with no extra call, and `fetch_documents` reads the
documents of the final hits with one RetrieveDocuments call per practical class (see
`retrieve_documents`). Collections made earlier have v1 rows and keep working,
with empty metadata and no private document fetch.

Stream (see the proto): the client sends Start; the server answers with RetrieveRecordsInfo
(one per part: a single node sends one; a router whose scope spans several nodes sends one per
node); the client builds each part's shape from (n_rows, m, row_size, hash_seed), sends the queries
of part 0, then part 1, and so on; the server streams one RecordAnswer per bucket, part by part.
Every part gets the full m and its own batch plan, so the sizes depend on the scope only. A cuckoo
failure drops the lowest-ranked row that does not fit and counts it; the call is never repeated.
"""

from __future__ import annotations

import queue
import random
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ombra import pir
from ombra.errors import CollectionError, OmbraError, collection_error, from_rpc_error
from ombra.probe import FIELDS_KEY, RESERVED_PREFIX
from ombra.ranges import RangeSchema
from ombra.retry import Replayable, retrying

API_VERSION = 1
KEY_CHUNK = 4_000_000  # below LIMIT_MAX_CHUNK_BYTES (4 MiB)
ROW_VERSION = 1  # the blob version the rows go with; part of every row's associated data
PIR_ID = RESERVED_PREFIX + b"pir"  # sealed marker blob: this collection has record columns
# Marker payload: magic "OPIR", u16 version, u16 reserved (0); version 2 adds u32 metadata_bytes.
# Version 1 (older collections): v1 rows, no metadata, no document columns.
_MARKER_V1 = b"OPIR\x01\x00\x00\x00"
_MARKER_HEAD = struct.Struct("<4sHH")
_MARKER_V2 = struct.Struct("<4sHHI")
MARKER_VERSION = 2
MAX_ROWS = 1 << 24  # most database rows a server may announce (bounds the client's own work)
MAX_PARTS = 1024
MAX_RETRIEVE_M = 3000  # LIMIT_MAX_RETRIEVE_M
_ERROR_KEYS_MISSING = 9
_ERROR_KEY_TAG_MISMATCH = 6

__all__ = [
    "PIR_ID",
    "ROW_VERSION",
    "Outcome",
    "Wanted",
    "exact_ints",
    "ints_from_meta",
    "n_ints_for",
    "retrieve",
    "seal_column",
]


def marker_payload(metadata_bytes: int | None = None) -> bytes:
    """The marker of a collection: v2 with its metadata slot, or (None) the v1 marker."""
    if metadata_bytes is None:
        return _MARKER_V1
    return _MARKER_V2.pack(b"OPIR", MARKER_VERSION, 0, metadata_bytes)


def parse_marker(payload: bytes) -> tuple[bool, int | None]:
    """(is a marker, metadata_bytes). metadata_bytes is None for a v1 marker (v1 rows)."""
    raw = bytes(payload)
    if raw == _MARKER_V1:
        return True, None
    if len(raw) == _MARKER_V2.size:
        magic, version, reserved, meta = _MARKER_V2.unpack(raw)
        if magic == b"OPIR" and version == MARKER_VERSION and reserved == 0:
            return True, meta
    return False, None


def is_marker(payload: bytes) -> bool:
    return parse_marker(payload)[0]


def n_ints_for(n_cards: int, schema: RangeSchema | None) -> int:
    """Integers in a row: none without range fields, else every filter value and every raw range
    value."""
    return 0 if schema is None else n_cards + len(schema.fields)


def exact_ints(
    fields: np.ndarray, metadata: Sequence[Mapping[str, Any]] | None, schema: RangeSchema | None
) -> np.ndarray:
    """int64[n, n_ints] of the rows of n vectors: their filter values, then raw range values."""
    n = len(fields)
    if schema is None:
        return np.zeros((n, 0), dtype=np.int64)
    if metadata is None or len(metadata) != n:
        raise ValueError("a collection with range fields needs one metadata dict per vector")
    nf = fields.shape[1]
    out = np.zeros((n, nf + len(schema.fields)), dtype=np.int64)
    out[:, :nf] = fields
    for i, m in enumerate(metadata):
        for j, f in enumerate(schema.fields):
            out[i, nf + j] = f.check_value(m[f.name])
    return out


def ints_from_meta(meta: Mapping[str, Any], schema: RangeSchema | None, n_cards: int) -> list[int]:
    """The integers of one row from a stored record's metadata (as `_put_records` wrote it)."""
    if schema is None:
        return []
    vals = meta.get(FIELDS_KEY)
    if vals is None or len(vals) != n_cards:
        raise ValueError("a stored record lacks its filter values")
    return [int(v) for v in vals] + [f.check_value(meta[f.name]) for f in schema.fields]


def seal_column(
    codec: pir.RowCodec,
    cluster: int,
    ids: Sequence[bytes],
    vecs: np.ndarray,
    ints: np.ndarray,
) -> bytes:
    """The record column of one block with no metadata or documents (v1 rows, or v2 rows that
    hold nothing but the vector): a sealed row per slot, in slot order."""
    return seal_block(codec, cluster, ids, vecs, ints).record


@dataclass
class BlockColumns:
    """What a private-retrieval collection stores next to one block."""

    record: bytes  # the ORCL record column
    documents: dict[int, bytes] = field(default_factory=dict)  # class -> ODCL document column


def seal_block(
    codec: pir.RowCodec,
    cluster: int,
    ids: Sequence[bytes],
    vecs: np.ndarray,
    ints: np.ndarray,
    metadata: Sequence[Mapping[str, Any]] | None = None,
    blobs: Sequence[bytes | None] | None = None,
) -> BlockColumns:
    """The record column and the document columns of one block, in slot order.

    `metadata[i]` is the user metadata of vector i (the row holds its compact JSON; it must fit
    the codec's slot, else PirError names the limit). `blobs[i]` is the stored document blob of
    vector i (the sealed bytes of the by-id blob) or None. A blob of a practical class goes into
    the block's column of that class, in block order, and its row holds (class, index in that
    column); a larger blob only sets class 3. Rows of a v1 codec hold neither.
    """
    n = len(ids)
    v32 = np.asarray(vecs, dtype="<f4")
    if codec.format == 1 and (blobs is not None or metadata is not None):
        raise pir.PirError("a v1 row codec cannot hold metadata or document pointers")
    rows: list[bytes] = []
    cols: dict[int, list[bytes]] = {c: [] for c in pir.PRACTICAL_CLASSES}
    for i in range(n):
        if codec.format == 1:
            rows.append(codec.seal(cluster, bytes(ids[i]), ROW_VERSION, v32[i], ints[i]))
            continue
        blob = None if blobs is None else blobs[i]
        cls = pir.doc_class_of(None if blob is None else len(blob))
        idx = 0
        if cls in cols:
            idx = len(cols[cls])
            cols[cls].append(bytes(blob))  # type: ignore[arg-type]
        meta = None if metadata is None else dict(metadata[i])
        rows.append(
            codec.seal(cluster, bytes(ids[i]), ROW_VERSION, v32[i], ints[i], meta, cls, idx)
        )
    version = 1 if codec.format == 1 else pir.COLUMN_VERSION
    return BlockColumns(
        pir.pack_column(rows, codec.row_size, version),
        {c: pir.pack_doc_column(r, c) for c, r in cols.items() if r},
    )


@dataclass(frozen=True)
class Wanted:
    """A candidate to fetch: where its row is, from the answers of the search."""

    ident: bytes
    cluster: int
    block: int  # index of its block among the blocks of the cluster, in the order SearchScores gave
    slot: int


@dataclass
class Outcome:
    rows: dict[bytes, pir.OpenedRow] = field(default_factory=dict, repr=False)
    asked: int = 0  # candidates that went into a plan
    dropped: int = 0  # lost to cuckoo failure (never retried)
    stale: int = 0  # candidates whose block is no longer what the search saw
    bad_rows: int = 0  # rows that did not authenticate
    filled: int = 0  # random rows added to reach m
    buckets: int = 0  # answers downloaded


class _NeedKeys(Exception):
    """The server has no retrieval keys, or other ones: upload them and try once more."""


class _Call:
    """One RetrieveRecords or RetrieveDocuments stream (`kind`): the requests come from a queue,
    because the queries can only be built once the first answer (the shape) is read."""

    def __init__(self, col: Any, start: Any, kind: str = "Records") -> None:
        self._pb = col.pb
        self._method = "Retrieve" + kind
        self._request = getattr(self._pb, self._method + "Request")
        self._q: queue.Queue = queue.Queue()
        self._q.put(self._request(api_version=API_VERSION, start=start))
        self._call = getattr(col.stub, self._method)(self._requests())
        self._ended = False

    def _requests(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            yield item

    def query(self, raw: bytes) -> None:
        self._q.put(self._request(api_version=API_VERSION, query=raw))

    def read(self) -> Any:
        try:
            return next(self._call)
        except StopIteration:
            raise CollectionError(0, "retrieval stream ended early") from None
        except OmbraError:
            raise
        except Exception as exc:
            mapped = from_rpc_error(exc, self._method, None)
            if mapped is None:
                raise
            raise mapped from exc

    def done_writing(self) -> None:
        """Half-close: no more requests. Safe to call more than once."""
        self._q.put(None)

    def close(self) -> None:
        self.done_writing()
        cancel = getattr(self._call, "cancel", None)
        if cancel is not None and not self._ended:
            cancel()
            return
        try:
            for _ in self._call:
                pass
        except Exception:  # noqa: S110  # the stream already ended with an error
            pass


def upload_keys(col: Any) -> None:
    """Send the public retrieval keys (context, relinearization, automorphism keys of all 13
    levels: one upload fits every shape) as one UploadKeys set. Only public material."""
    pb = col.pb
    pc = col.kit.pir
    if pc is None:
        raise CollectionError(0, "this key file has no private retrieval key")
    named = pc.key_files(col.kit.openfhe_version)
    files = {
        pb.KEY_KIND_PIR_CONTEXT: named["pir_context"],
        pb.KEY_KIND_PIR_EVAL_MULT: named["pir_eval_mult"],
        pb.KEY_KIND_PIR_AUTOMORPHISM: named["pir_automorphism"],
    }

    def chunks():
        first = True
        for kind, data in files.items():
            for off in range(0, len(data), KEY_CHUNK):
                c = pb.UploadKeysChunk(
                    api_version=API_VERSION,
                    kind=kind,
                    offset=off,
                    data=data[off : off + KEY_CHUNK],
                    last=off + KEY_CHUNK >= len(data),
                )
                if first:
                    c.collection = col.name
                    first = False
                yield c

    err = col.stub.UploadKeys(Replayable(chunks)).error
    if err.code != 0:
        raise collection_error(err.code, err.message)
    col._pir_uploaded = pc.key_tag


def retrieve(
    col: Any,
    wanted: Sequence[Wanted],
    ids_of: Callable[[int, int], Sequence[bytes] | None],
    *,
    scope: Sequence[int] | None,
    m: int,
    rng: random.Random,
) -> Outcome:
    """Fetch the rows of `wanted` (best first) with one RetrieveRecords call.

    `scope` is the list of clusters to take the database from, None for all of them. `ids_of(c, b)`
    gives the ids of block b of cluster c as the search saw them (None if unknown). At most `m`
    candidates are asked for; the rest of the m rows are random real rows. Returns the opened rows
    by id. If the server has no retrieval keys (or other ones), they are uploaded once and the
    call is repeated once.
    """
    if col.kit.pir is None:
        raise CollectionError(0, "this key file has no private retrieval key")
    if not 1 <= m:
        raise ValueError("m must be positive")
    return _with_keys(
        col,
        "RetrieveRecords",
        lambda may_upload: _once(col, wanted, ids_of, scope, m, rng, may_upload),
    )


def _with_keys(col: Any, method: str, once: Callable[[bool], Any]) -> Any:
    """Run `once(may_upload)` under the retry policy; if the server has no retrieval keys (or other
    ones), upload them and run it once more."""
    for attempt in (0, 1):
        may_upload = attempt == 0
        try:
            return retrying(col.stub, method, lambda may_upload=may_upload: once(may_upload))
        except _NeedKeys:
            upload_keys(col)
    raise AssertionError("unreachable")  # pragma: no cover


def _once(
    col: Any,
    wanted: Sequence[Wanted],
    ids_of: Callable[[int, int], Sequence[bytes] | None],
    scope: Sequence[int] | None,
    m: int,
    rng: random.Random,
    may_upload: bool,
) -> Outcome:
    pb, pc, codec = col.pb, col.kit.pir, col.row_codec
    seed = bytes(rng.getrandbits(8) for _ in range(pir.SEED_LEN))
    start = pb.RetrieveRecordsStart(
        collection=col.name,
        key_tag=col.kit.key_tag,
        pir_key_tag=pc.key_tag,
        m=m,
        hash_seed=seed,
        n_queries=0,  # as many as the shape of each part needs
    )
    if scope is None:
        start.all_clusters = True
    else:
        start.cluster_ids.extend(sorted(set(int(c) for c in scope)))
    call = _Call(col, start)
    out = Outcome()
    try:
        infos = _read_infos(call, may_upload)
        parts = _plan_parts(infos, wanted, ids_of, m, seed, rng, codec, out)
        for shape, plan, _w in parts:
            for q in pc.with_shape(shape).build_queries(plan):
                call.query(q)
        call.done_writing()  # the server answers once the request side is closed
        for p, (shape, plan, w) in enumerate(parts):
            answers = []
            for b in range(shape.buckets):
                resp = call.read()
                which = resp.WhichOneof("part")
                if which == "error":
                    raise collection_error(resp.error.code, resp.error.message)
                if which != "answer" or resp.answer.bucket != b or resp.answer.part != p:
                    raise CollectionError(0, "retrieval answers are out of order")
                answers.append(bytes(resp.answer.answer))
            out.buckets += len(answers)
            _open_rows(pc.with_shape(shape), plan, answers, w, codec, out)
        call._ended = True
    finally:
        call.close()
    return out


def _read_infos(call: _Call, may_upload: bool) -> list[Any]:
    infos: list[Any] = []
    n_parts = 1
    while len(infos) < n_parts:
        resp = call.read()
        which = resp.WhichOneof("part")
        if which == "error":
            call._ended = True
            if may_upload and resp.error.code in (_ERROR_KEYS_MISSING, _ERROR_KEY_TAG_MISMATCH):
                raise _NeedKeys
            raise collection_error(resp.error.code, resp.error.message)
        if which != "info":
            raise CollectionError(0, "retrieval stream began with something other than its info")
        info = resp.info
        n_parts = max(1, int(info.n_parts))
        if n_parts > MAX_PARTS or info.part != len(infos):
            raise CollectionError(0, "retrieval info is out of order")
        infos.append(info)
    return infos


def _plan_parts(
    infos: list[Any],
    wanted: Sequence[Wanted],
    ids_of: Callable[[int, int], Sequence[bytes] | None],
    m: int,
    seed: bytes,
    rng: random.Random,
    codec: pir.RowCodec,
    out: Outcome,
) -> list[tuple[pir.PirShape, pir.BatchPlan, dict[bytes, tuple[int, int]]]]:
    """One shape and plan per part. Candidates go to the part whose spans hold their cluster."""
    parts = []
    owner: dict[int, int] = {}  # cluster -> part
    for p, info in enumerate(infos):
        for sp in info.spans:
            owner.setdefault(int(sp.cluster_id), p)
    per_part: list[list[Wanted]] = [[] for _ in infos]
    for w in wanted:
        p = owner.get(w.cluster)
        if p is None:
            out.stale += 1
        else:
            per_part[p].append(w)
    for p, info in enumerate(infos):
        spans = list(info.spans)
        if (
            not 1 <= info.n_rows <= MAX_ROWS
            or info.row_size != codec.row_size
            or sum(int(s.n_rows) for s in spans) != info.n_rows
        ):
            raise CollectionError(0, "the server describes a database that does not fit")
        nxt = 0
        by_cluster: dict[int, list[Any]] = {}
        for sp in spans:
            if int(sp.first_row) != nxt:
                raise CollectionError(0, "the server's block layout has gaps")
            nxt += int(sp.n_rows)
            by_cluster.setdefault(int(sp.cluster_id), []).append(sp)
        try:
            shape = pir.make_shape(int(info.n_rows), m, int(info.row_size), seed)
        except pir.PirError as exc:
            raise CollectionError(0, f"the server's retrieval shape is unusable: {exc}") from exc
        if info.n_buckets != shape.buckets:
            raise CollectionError(0, "the server's bucket count differs from the client's")
        rows: dict[bytes, tuple[int, int]] = {}  # id -> (database row, cluster)
        for w in per_part[p]:
            blocks = by_cluster.get(w.cluster, [])
            ids = ids_of(w.cluster, w.block)
            ok = w.block < len(blocks) and ids is not None
            if ok:
                sp = blocks[w.block]
                ok = int(sp.n_rows) == len(ids) and w.slot < len(ids) and ids[w.slot] == w.ident
            if ok:
                rows[w.ident] = (int(sp.first_row) + w.slot, w.cluster)
            else:
                out.stale += 1  # the block changed since the search: do not guess
        plan = pir.plan_batch(shape, [r for r, _ in rows.values()], rng)
        out.asked += len(plan.targets)
        out.dropped += len(plan.dropped)
        out.filled += len(plan.filled)
        parts.append((shape, plan, rows))
    return parts


def _open_rows(
    pc: pir.PirClient,
    plan: pir.BatchPlan,
    answers: list[bytes],
    rows: dict[bytes, tuple[int, int]],
    codec: pir.RowCodec,
    out: Outcome,
) -> None:
    try:
        raw = pc.decode_answers(plan, answers)
    except pir.PirError as exc:
        raise CollectionError(0, "a retrieval answer did not decode") from exc
    except Exception as exc:  # a malformed ciphertext from the server
        raise CollectionError(0, "a retrieval answer could not be read") from exc
    placed = set(plan.targets)
    for ident, (row, cluster) in rows.items():
        if row not in placed:
            continue  # dropped (counted when planned)
        try:
            out.rows[ident] = codec.open_row(raw[row], cluster, ident, ROW_VERSION)
        except pir.PirError:
            out.bad_rows += 1


# ---------------------------------------------------------------------------- documents


@dataclass(frozen=True)
class DocWanted:
    """A document to fetch: the block of its vector as the search saw it, and the index of the
    document in that block's column of the class (from the vector's record row)."""

    ident: bytes
    cluster: int
    block: int
    index: int


@dataclass
class DocOutcome:
    blobs: dict[bytes, bytes] = field(default_factory=dict, repr=False)  # id -> stored blob
    absent: bool = False  # the collection holds no document of this class: no call was made
    asked: int = 0  # documents that went into a plan
    dropped: int = 0  # lost to cuckoo failure (never retried)
    stale: int = 0  # whose block or index is no longer what the search saw
    filled: int = 0  # random real rows added to reach m
    answers: int = 0  # answer messages downloaded (all planes)


def retrieve_documents(
    col: Any,
    doc_class: int,
    wanted: Sequence[DocWanted],
    *,
    scope: Sequence[int] | None,
    m: int,
    rng: random.Random,
) -> DocOutcome:
    """Fetch the stored document blobs of `wanted` with one RetrieveDocuments call of class
    `doc_class` (1: 16 KiB, 2: 256 KiB). Same scope and the same fill rule as `retrieve`: at most
    `m` wanted documents, the rest of the m rows random real rows of the scope, a cuckoo failure
    dropped and counted. The call is made even when `wanted` is empty, so that its shape does not
    depend on the hits. A collection with no document of the class answers with an empty Info and
    nothing is fetched (`absent`).
    """
    if col.kit.pir is None:
        raise CollectionError(0, "this key file has no private retrieval key")
    if doc_class not in pir.DOC_CLASS_SIZES:
        raise ValueError("documents of this class are not fetched privately")
    if m < 1:
        raise ValueError("m must be positive")
    return _with_keys(
        col,
        "RetrieveDocuments",
        lambda may_upload: _once_documents(col, doc_class, wanted, scope, m, rng, may_upload),
    )


def _once_documents(
    col: Any,
    doc_class: int,
    wanted: Sequence[DocWanted],
    scope: Sequence[int] | None,
    m: int,
    rng: random.Random,
    may_upload: bool,
) -> DocOutcome:
    pb, pc = col.pb, col.kit.pir
    size = pir.DOC_CLASS_SIZES[doc_class]
    seed = bytes(rng.getrandbits(8) for _ in range(pir.SEED_LEN))
    start = pb.RetrieveDocumentsStart(
        collection=col.name,
        key_tag=col.kit.key_tag,
        pir_key_tag=pc.key_tag,
        m=m,
        hash_seed=seed,
        n_queries=0,
        doc_class=doc_class,
    )
    if scope is None:
        start.all_clusters = True
    else:
        start.cluster_ids.extend(sorted(set(int(c) for c in scope)))
    call = _Call(col, start, "Documents")
    out = DocOutcome()
    try:
        infos = _read_infos(call, may_upload)
        if all(int(i.n_rows) == 0 for i in infos):
            out.absent = True
            call._ended = True
            return out
        parts = _plan_document_parts(infos, wanted, m, seed, size, rng, out)
        for doc, plan, _rows in parts:
            for q in pc.with_shape(doc.shape).build_queries(plan):
                call.query(q)
        call.done_writing()  # the server answers after the half-close
        for p, (doc, plan, rows) in enumerate(parts):
            answers: list[bytes] = []
            for plane in range(doc.planes):
                for b in range(doc.shape.buckets):
                    resp = call.read()
                    which = resp.WhichOneof("part")
                    if which == "error":
                        raise collection_error(resp.error.code, resp.error.message)
                    a = resp.answer if which == "answer" else None
                    if a is None or (a.plane, a.bucket, a.part) != (plane, b, p):
                        raise CollectionError(0, "document answers are out of order")
                    answers.append(bytes(a.answer))
            out.answers += len(answers)
            try:
                got = pc.with_shape(doc.shape).decode_document_answers(plan, answers, doc)
            except pir.PirError as exc:
                raise CollectionError(0, "a document answer did not decode") from exc
            except Exception as exc:  # a malformed ciphertext from the server
                raise CollectionError(0, "a document answer could not be read") from exc
            for ident, row in rows.items():
                if row in got:
                    out.blobs[ident] = got[row]
        call._ended = True
    finally:
        call.close()
    return out


def _plan_document_parts(
    infos: list[Any],
    wanted: Sequence[DocWanted],
    m: int,
    seed: bytes,
    size: int,
    rng: random.Random,
    out: DocOutcome,
) -> list[tuple[pir.DocShape, pir.BatchPlan, dict[bytes, int]]]:
    """One shape and plan per part. A document goes to the part whose spans hold its cluster."""
    owner: dict[int, int] = {}
    for p, info in enumerate(infos):
        for sp in info.spans:
            owner.setdefault(int(sp.cluster_id), p)
    per_part: list[list[DocWanted]] = [[] for _ in infos]
    for w in wanted:
        p = owner.get(w.cluster)
        if p is None:
            out.stale += 1
        else:
            per_part[p].append(w)
    planes, _row = pir.plane_layout(size)
    parts = []
    for p, info in enumerate(infos):
        spans = list(info.spans)
        total = sum(int(s.n_rows) for s in spans)
        n = int(info.n_rows)
        if (
            not 1 <= n <= MAX_ROWS
            or info.row_size != size
            or info.n_planes != planes
            or not (
                total == n or (total == 0 and n == 1)
            )  # one padding row when the scope is empty
        ):
            raise CollectionError(0, "the server describes a database that does not fit")
        nxt = 0
        by_cluster: dict[int, list[Any]] = {}
        for sp in spans:
            if int(sp.first_row) != nxt:
                raise CollectionError(0, "the server's block layout has gaps")
            nxt += int(sp.n_rows)
            by_cluster.setdefault(int(sp.cluster_id), []).append(sp)
        try:
            doc = pir.make_doc_shape(n, m, size, seed)
        except pir.PirError as exc:
            raise CollectionError(0, f"the server's retrieval shape is unusable: {exc}") from exc
        if info.n_buckets != doc.shape.buckets:
            raise CollectionError(0, "the server's bucket count differs from the client's")
        rows: dict[bytes, int] = {}
        for w in per_part[p]:
            blocks = by_cluster.get(w.cluster, [])
            if w.block < len(blocks) and w.index < int(blocks[w.block].n_rows):
                rows[w.ident] = int(blocks[w.block].first_row) + w.index
            else:
                out.stale += 1  # the block changed since the search: do not guess
        plan = pir.plan_batch(doc.shape, list(rows.values()), rng)
        out.asked += len(plan.targets)
        out.dropped += len(plan.dropped)
        out.filled += len(plan.filled)
        parts.append((doc, plan, rows))
    return parts
