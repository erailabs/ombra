# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Record columns on the CPU `ombra_server`: Insert with a column (in parts), a refused column,
survival over a kill and restart, the RetrieveRecords stub. Synthetic data only.

There is no call that returns a stored column yet, so the test reads the server's own file
(`records/<segment>.orec`), whose layout is fixed (container header, then the ORCL column).
"""

from __future__ import annotations

import struct
import zlib

import pytest

np = pytest.importorskip("numpy")
grpc = pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")
pytest.importorskip("openfhe")

from server_harness import ServerProcess, find_server_binary  # noqa: E402

pytestmark = pytest.mark.openfhe

if find_server_binary() is None:
    pytest.skip("ombra_server (CPU build) not found", allow_module_level=True)

from ombra._proto import load_stubs  # noqa: E402

NAME = "rec_col"
D = 384


def _column(row_size: int, n_rows: int, fill: int) -> bytes:
    return (
        b"ORCL" + struct.pack("<HHII", 1, 0, row_size, n_rows) + bytes([fill]) * (row_size * n_rows)
    )


def test_insert_with_record_column(tmp_path) -> None:
    pb, pbg = load_stubs()
    rng = np.random.default_rng(3)
    x = rng.normal(size=(8, D))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    server = ServerProcess(tmp_path)
    with server:
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
        stub = pbg.OmbraStub(grpc.insecure_channel(server.address))
        blocks = stub.ListBlocks(pb.ListBlocksRequest(api_version=1, collection=NAME)).blocks
        data = [b for b in blocks if b.n_vectors and b.cluster_id == 0] or list(blocks)
        src = data[0]
        parts = list(
            stub.GetBlock(pb.GetBlockRequest(api_version=1, collection=NAME, segment=src.segment))
        )
        cols = [p.column for p in parts if p.WhichOneof("part") == "column"]

        def insert(ids: list[bytes], record: bytes | None, split: int = 0):
            def gen():
                yield pb.InsertRequest(
                    api_version=1,
                    header=pb.InsertHeader(collection=NAME, cluster_id=src.cluster_id, ids=ids),
                )
                for c in cols:
                    yield pb.InsertRequest(api_version=1, column=c)
                if record is not None:
                    cut = split or len(record)
                    for i in range(0, len(record), cut):
                        yield pb.InsertRequest(api_version=1, record_column=record[i : i + cut])

            return stub.Insert(gen())

        good = _column(128, 2, 0xAB)
        r = insert([b"n1", b"n2"], good, split=100)  # joined from several parts
        assert not r.HasField("error") and r.inserted == 2
        files = sorted((server.data_dir / "records").glob("*.orec"))
        assert len(files) == 1
        raw = files[0].read_bytes()
        assert raw[:4] == b"OREC" and struct.unpack_from("<H", raw, 4)[0] == 1
        assert struct.unpack_from("<I", raw, 6)[0] == zlib.crc32(raw[10:])
        assert raw[10:] == good

        # wrong row count, bad header: refused, nothing stored
        r = insert([b"m1", b"m2", b"m3"], good)
        assert r.error.code == pb.ERROR_INVALID_ARGUMENT
        r = insert([b"m1"], b"XXXX" + good[4:])
        assert r.error.code == pb.ERROR_INVALID_ARGUMENT
        assert len(list((server.data_dir / "records").glob("*.orec"))) == 1

        # a block without a column works as before
        r = insert([b"p1"], None)
        assert not r.HasField("error") and r.inserted == 1
        assert len(list((server.data_dir / "records").glob("*.orec"))) == 1

        # a repeat with the same column is a no-op success; with another column it is refused
        assert insert([b"n1", b"n2"], good).inserted == 2
        assert insert([b"n1", b"n2"], _column(128, 2, 1)).error.code == pb.ERROR_INVALID_ARGUMENT

        # the stub: a valid call is read to the end, then UNIMPLEMENTED; a bad one gets an error
        def call(start: pb.RetrieveRecordsStart, n_queries: int):
            def gen():
                yield pb.RetrieveRecordsRequest(api_version=1, start=start)
                for _ in range(n_queries):
                    yield pb.RetrieveRecordsRequest(api_version=1, query=b"\x01" * 16)

            return list(stub.RetrieveRecords(gen()))

        start = pb.RetrieveRecordsStart(
            collection=NAME, key_tag="t", pir_key_tag="p", all_clusters=True, m=4,
            hash_seed=b"s" * 16, n_queries=1,
        )  # fmt: skip
        with pytest.raises(grpc.RpcError) as e:
            call(start, 1)
        assert e.value.code() == grpc.StatusCode.UNIMPLEMENTED
        start.hash_seed = b"short"
        out = call(start, 1)
        assert out[0].error.code == pb.ERROR_INVALID_ARGUMENT
        start.hash_seed = b"s" * 16
        start.ClearField("all_clusters")
        start.cluster_ids.append(1)  # the collection has one data cluster: id 1 is out of range
        out = call(start, 1)
        assert out[0].error.code == pb.ERROR_INVALID_ARGUMENT
        start.collection = "no_such_collection"
        start.ClearField("cluster_ids")
        start.all_clusters = True
        out = call(start, 1)
        assert out[0].error.code == pb.ERROR_COLLECTION_NOT_FOUND

        server.restart(kill=True)  # the file is durable
        assert len(list((server.data_dir / "records").glob("*.orec"))) == 1
