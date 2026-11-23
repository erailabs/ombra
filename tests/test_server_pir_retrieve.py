# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""RetrieveRecords on the CPU `ombra_server`, over gRPC with real OpenFHE keys.

The server answers on OpenFHE (C++); the answers must decrypt to exactly what the Python reference
(`ombra.pir_reference.PirServerReference`) gives for the same inputs, and the retrieved rows must be
the inserted ones. Stream order: Start, then the Info, then the queries, then the answers. Synthetic
data only, a few rows, m of 1 to 3.
"""

from __future__ import annotations

import queue
import random

import pytest

np = pytest.importorskip("numpy")
grpc = pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")
pytest.importorskip("openfhe")

from server_harness import ServerProcess, find_server_binary  # noqa: E402

pytestmark = pytest.mark.openfhe

if find_server_binary() is None:
    pytest.skip("ombra_server (CPU build) not found", allow_module_level=True)

from ombra import pir  # noqa: E402
from ombra.file_format import Kind, read_envelope  # noqa: E402
from ombra.pir_reference import PirServerReference  # noqa: E402

NAME = "pir_retrieve"
D = 384
CHUNK = 1 << 20
ROW = 6400  # three rows per plaintext, so a bucket's grid has several cells
SEED = b"0123456789abcdef"


def _column(rows: list[bytes]) -> bytes:
    return pir.pack_column(rows, ROW)


def _rows(n: int, tag: int) -> list[bytes]:
    rng = np.random.default_rng(tag)
    return [rng.integers(0, 256, ROW, dtype=np.uint8).tobytes() for _ in range(n)]


def _upload(t, collection: str, files: dict[int, bytes]):
    def chunks():
        first = True
        for kind, data in files.items():
            for off in range(0, max(len(data), 1), CHUNK):
                yield t.pb.UploadKeysChunk(
                    api_version=1,
                    collection=collection if first else "",
                    kind=kind,
                    offset=off,
                    data=data[off : off + CHUNK],
                    last=off + CHUNK >= len(data),
                )
                first = False

    return t.stub.UploadKeys(chunks())


class Call:
    """One RetrieveRecords call whose requests are fed from the test, so the Info can be read
    before the queries are sent."""

    def __init__(self, stub, pb) -> None:
        self.pb = pb
        self._q: queue.Queue = queue.Queue()
        self.call = stub.RetrieveRecords(self._gen())

    def _gen(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            yield item

    def start(self, **kw) -> None:
        self._q.put(
            self.pb.RetrieveRecordsRequest(
                api_version=1, start=self.pb.RetrieveRecordsStart(**kw)
            )
        )

    def queries(self, qs: list[bytes]) -> None:
        for q in qs:
            self._q.put(self.pb.RetrieveRecordsRequest(api_version=1, query=q))

    def end(self) -> None:
        self._q.put(None)

    def next(self):
        return next(self.call)

    def rest(self) -> list:
        return list(self.call)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("pir_retrieve")
    rng = np.random.default_rng(5)
    x = rng.normal(size=(8, D))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    server = ServerProcess(tmp_path)
    server.start()
    try:
        client = server.client()
        client.create_collection(
            NAME,
            x,
            [f"v{i}" for i in range(8)],
            key_file=tmp_path / "k.key",
            passphrase="pw",  # noqa: S106
            preset="fast",
            n_clusters=1,
            scrypt_params=(2**4, 8, 1),
        )
        t = client._core._t
        pb, stub = t.pb, t.stub
        key_tag = stub.GetCollection(pb.GetCollectionRequest(name=NAME)).info.spec.key_tag

        blocks = stub.ListBlocks(pb.ListBlocksRequest(api_version=1, collection=NAME)).blocks
        src = next(b for b in blocks if b.n_vectors and b.cluster_id == 0)
        parts = list(
            stub.GetBlock(pb.GetBlockRequest(api_version=1, collection=NAME, segment=src.segment))
        )
        header = next(p.header for p in parts if p.WhichOneof("part") == "header")
        cols = [p.column for p in parts if p.WhichOneof("part") == "column"]
        rows_a, rows_b = _rows(len(header.ids), 1), _rows(3, 2)

        def replace():
            yield pb.ReplaceBlockRequest(
                api_version=1,
                header=pb.ReplaceBlockHeader(
                    collection=NAME, segments=[src.segment], ids=list(header.ids)
                ),
            )
            for c in cols:
                yield pb.ReplaceBlockRequest(api_version=1, column=c)
            yield pb.ReplaceBlockRequest(api_version=1, record_column=_column(rows_a))

        r = stub.ReplaceBlock(replace())
        assert not r.HasField("error"), r.error
        seg_a = r.segment

        def insert():
            yield pb.InsertRequest(
                api_version=1,
                header=pb.InsertHeader(collection=NAME, cluster_id=0, ids=[b"n1", b"n2", b"n3"]),
            )
            for c in cols:
                yield pb.InsertRequest(api_version=1, column=c)
            yield pb.InsertRequest(api_version=1, record_column=_column(rows_b))

        r = stub.Insert(insert())
        assert not r.HasField("error") and r.inserted == 3
        yield {
            "server": server, "client": client, "t": t, "pb": pb, "stub": stub,
            "key_tag": key_tag, "rows": rows_a + rows_b, "seg_a": seg_a,
            "spans": (len(rows_a), 3),
        }  # fmt: skip
    finally:
        server.stop()


def _shape(world, m: int, seed: bytes = SEED) -> pir.PirShape:
    return pir.make_shape(len(world["rows"]), m, ROW, seed)


def _start(world, pir_tag: str, m: int, seed: bytes = SEED, **extra):
    kw = dict(
        collection=NAME, key_tag=world["key_tag"], pir_key_tag=pir_tag, all_clusters=True,
        m=m, hash_seed=seed, n_queries=0,
    )  # fmt: skip
    kw.update(extra)
    return kw


def _upload_keys(world, pc: pir.PirClient) -> str:
    t, pb = world["t"], world["pb"]
    files = pc.key_files(t.openfhe_version)
    r = _upload(
        t,
        NAME,
        {
            pb.KEY_KIND_PIR_CONTEXT: files["pir_context"],
            pb.KEY_KIND_PIR_EVAL_MULT: files["pir_eval_mult"],
            pb.KEY_KIND_PIR_AUTOMORPHISM: files["pir_automorphism"],
        },
    )
    assert not r.HasField("error"), r.error
    world["files"] = files
    return pc._keys.publicKey.GetKeyTag()


def _payload(world, name: str) -> bytes:
    kind = Kind.CONTEXT if name == "pir_context" else Kind.PUBLIC_EVAL_KEY
    return read_envelope(
        world["files"][name], kind, pir.PIR_PRESET, world["t"].openfhe_version
    ).payload


def test_retrieve(world) -> None:
    pb, stub, t = world["pb"], world["stub"], world["t"]
    rows = world["rows"]
    n_a, n_b = world["spans"]
    m = 2
    shape = _shape(world, m)
    assert (shape.r1, shape.r2) != (1, 1)  # a real grid
    pc = pir.PirClient(shape)

    # No retrieval keys stored yet: KEYS_MISSING, and no Info.
    call = Call(stub, pb)
    call.start(**_start(world, "whatever", m))
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_KEYS_MISSING
    call.end()

    tag = _upload_keys(world, pc)

    # The main path: Info first, then the queries, then one answer per bucket.
    plan = pir.plan_batch(shape, [3, 9, 12][:m], random.Random(1))
    queries = pc.build_queries(plan)
    call = Call(stub, pb)
    call.start(**_start(world, tag, m))
    info = call.next()
    assert info.WhichOneof("part") == "info"
    i = info.info
    assert (i.n_rows, i.row_size, i.n_buckets) == (len(rows), ROW, shape.buckets)
    assert (i.part, i.n_parts) == (0, 1)
    assert [(s.cluster_id, s.first_row, s.n_rows) for s in i.spans] == [
        (0, 0, n_a),
        (0, n_a, n_b),
    ]
    assert i.spans[0].segment == world["seg_a"]
    call.queries(queries)
    call.end()
    got = call.rest()
    assert [r.WhichOneof("part") for r in got] == ["answer"] * shape.buckets
    assert [r.answer.bucket for r in got] == list(range(shape.buckets))
    assert all(r.answer.part == 0 for r in got)
    answers = [r.answer.answer for r in got]

    # The rows retrieved are the inserted ones.
    decoded = pc.decode_answers(plan, answers)
    assert set(decoded) == set(plan.targets) and plan.targets
    for row, data in decoded.items():
        assert data == rows[row]

    # The answers decrypt to the same coefficients as the Python reference gives.
    ref = PirServerReference(
        pc.context_bytes(),
        _payload(world, "pir_eval_mult"),
        _payload(world, "pir_automorphism"),
        pir.pack_column(rows, ROW),
        m,
        SEED,
    )
    want = ref.answer(queries)
    assert len(want) == len(answers)
    ofhe = pc._ofhe
    for b, (a, w) in enumerate(zip(answers, want, strict=True)):
        decs = []
        for raw in (a, w):
            ct = ofhe.DeserializeCiphertextString(raw, ofhe.BINARY)
            pt = pc.cc.Decrypt(ct, pc._keys.secretKey)
            pt.SetLength(shape.pp.ring_dim)
            decs.append([v % shape.pp.plain_modulus for v in pt.GetCoefPackedValue()])
        assert decs[0] == decs[1], f"bucket {b} differs from the reference"

    # Nothing is stored by the call: the key directory still holds the three search and three
    # retrieval files.
    assert len(list((world["server"].data_dir / "keys").iterdir())) == 6

    # A non-zero n_queries equal to the shape's is accepted.
    call = Call(stub, pb)
    call.start(**_start(world, tag, m, n_queries=shape.n_cts))
    assert call.next().WhichOneof("part") == "info"
    call.queries(queries)
    call.end()
    assert len(call.rest()) == shape.buckets

    # ... and one that differs is INVALID_ARGUMENT (no Info).
    call = Call(stub, pb)
    call.start(**_start(world, tag, m, n_queries=shape.n_cts + 1))
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_INVALID_ARGUMENT
    call.end()

    # A wrong tag for the stored keys.
    call = Call(stub, pb)
    call.start(**_start(world, "another-tag", m))
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_KEY_TAG_MISMATCH
    call.end()

    # The collection's key tag is checked too.
    call = Call(stub, pb)
    call.start(**_start(world, tag, m, key_tag="not-the-collection-tag"))
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_KEY_TAG_MISMATCH
    call.end()

    # A scope with one cluster list gives the same database as all clusters here.
    call = Call(stub, pb)
    call.start(**_start(world, tag, m, all_clusters=False, cluster_ids=[0]))
    assert call.next().info.n_rows == len(rows)
    call.queries(queries)
    call.end()
    assert len(call.rest()) == shape.buckets

    # Too many queries after the Info: BAD_SEQUENCE; too few: BAD_SEQUENCE.
    call = Call(stub, pb)
    call.start(**_start(world, tag, m))
    call.next()
    call.queries(queries + queries[:1])
    call.end()
    out = call.rest()
    assert out and out[-1].error.code == pb.ERROR_BAD_SEQUENCE
    call = Call(stub, pb)
    call.start(**_start(world, tag, m))
    call.next()
    call.end()
    out = call.rest()
    assert out and out[-1].error.code == pb.ERROR_BAD_SEQUENCE

    # A query that is not a ciphertext, or one made under another key.
    call = Call(stub, pb)
    call.start(**_start(world, tag, m))
    call.next()
    call.queries([b"\x01" * 64] * shape.n_cts)
    call.end()
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_INVALID_ARGUMENT
    other = pir.PirClient(shape)
    foreign = other.build_queries(plan)
    call = Call(stub, pb)
    call.start(**_start(world, tag, m))
    call.next()
    call.queries(foreign)
    call.end()
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_INVALID_ARGUMENT

    # A CKKS search still gets as far as the engine after retrieval calls (this build has no GPU,
    # so it must fail only with the documented message, not with a key error).
    with pytest.raises(Exception, match="without GPU support"):
        world["client"].search(NAME, np.ones(D) / np.sqrt(D), k=3)


def test_retrieve_other_shapes(world) -> None:
    """m of 1 and 3, another seed: the layout the server derives is the client's."""
    pb, stub, t = world["pb"], world["stub"], world["t"]
    rows = world["rows"]
    for m, seed, cands in ((1, b"\x00" * 16, [7]), (3, b"abcdefghijklmnop", [0, 10, 5])):
        shape = _shape(world, m, seed)
        pc = pir.PirClient(shape)
        tag = _upload_keys(world, pc)
        plan = pir.plan_batch(shape, cands, random.Random(m))
        queries = pc.build_queries(plan)
        call = Call(stub, pb)
        call.start(**_start(world, tag, m, seed))
        assert call.next().info.n_buckets == shape.buckets
        call.queries(queries)
        call.end()
        answers = [r.answer.answer for r in call.rest()]
        decoded = pc.decode_answers(plan, answers)
        assert decoded and all(rows[r] == d for r, d in decoded.items())


def test_block_without_a_record_column(world) -> None:
    """A block in scope without a record column is BLOCK_MISMATCH."""
    pb, stub = world["pb"], world["stub"]
    shape = _shape(world, 1)
    pc = pir.PirClient(shape)
    tag = _upload_keys(world, pc)
    def insert():
        yield pb.InsertRequest(
            api_version=1, header=pb.InsertHeader(collection=NAME, cluster_id=0, ids=[b"z1"])
        )
        blocks = stub.ListBlocks(pb.ListBlocksRequest(api_version=1, collection=NAME)).blocks
        src = next(b for b in blocks if b.n_vectors and b.cluster_id == 0)
        for p in stub.GetBlock(
            pb.GetBlockRequest(api_version=1, collection=NAME, segment=src.segment)
        ):
            if p.WhichOneof("part") == "column":
                yield pb.InsertRequest(api_version=1, column=p.column)

    r = stub.Insert(insert())
    assert not r.HasField("error")
    call = Call(stub, pb)
    call.start(**_start(world, tag, 1))
    out = call.rest()
    assert len(out) == 1 and out[0].error.code == pb.ERROR_BLOCK_MISMATCH
    call.end()
