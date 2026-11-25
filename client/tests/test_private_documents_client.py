# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private document fetch, client parts that need no OpenFHE: marker versions, the columns of a
block, the Insert stream with document columns, the proto. Synthetic data only."""

from __future__ import annotations

import numpy as np
import pytest
from ombra import pir
from ombra import private_retrieval as pr

KEY = bytes(range(32))


def test_marker_v1_and_v2_are_both_read() -> None:
    assert pr.parse_marker(pr.marker_payload()) == (True, None)  # v1: v1 rows
    p = pr.marker_payload(512)
    assert p[:4] == b"OPIR" and pr.parse_marker(p) == (True, 512)
    assert pr.is_marker(p) and pr.is_marker(pr.marker_payload())
    assert not pr.is_marker(b"OPIR\x03\x00\x00\x00\x00\x00\x00\x00")
    assert not pr.is_marker(b"junk")


def test_seal_block_puts_each_blob_in_its_class_column_in_block_order() -> None:
    c = pir.RowCodec(KEY, "coll", 8, 0, 64)
    ids = [b"a", b"b", b"c", b"d", b"e"]
    vecs = np.random.default_rng(1).standard_normal((5, 8)).astype("<f4")
    small, mid, big = b"s" * 16384, b"m" * 262144, b"L" * 2097152
    blobs = [small, None, mid, big, small[::-1]]
    metas = [{"i": i} for i in range(5)]
    cols = pr.seal_block(c, 4, ids, vecs, np.zeros((5, 0), dtype=np.int64), metas, blobs)
    rec = pir.parse_column(cols.record)
    assert rec.version == 2 and rec.n_rows == 5
    got = [c.open_row(rec.row(i), 4, ids[i], pr.ROW_VERSION) for i in range(5)]
    assert [(g.doc_class, g.doc_index) for g in got] == [(1, 0), (0, 0), (2, 0), (3, 0), (1, 1)]
    assert [g.metadata for g in got] == metas
    assert sorted(cols.documents) == [1, 2]  # no column for the large class
    d1 = pir.parse_doc_column(cols.documents[1])
    assert [d1.row(0), d1.row(1)] == [small, small[::-1]]
    assert pir.parse_doc_column(cols.documents[2]).row(0) == mid


def test_seal_block_without_documents_has_no_document_columns() -> None:
    c = pir.RowCodec(KEY, "coll", 8, 0, 64)
    vecs = np.zeros((2, 8), dtype="<f4")
    cols = pr.seal_block(c, 0, [b"a", b"b"], vecs, np.zeros((2, 0), dtype=np.int64))
    assert cols.documents == {} and pir.parse_column(cols.record).n_rows == 2


def test_seal_block_v1_codec_stays_v1() -> None:
    c = pir.RowCodec(KEY, "coll", 8, 0)
    vecs = np.zeros((1, 8), dtype="<f4")
    cols = pr.seal_block(c, 0, [b"a"], vecs, np.zeros((1, 0), dtype=np.int64))
    assert pir.parse_column(cols.record).version == 1 and cols.documents == {}
    with pytest.raises(pir.PirError):
        pr.seal_block(c, 0, [b"a"], vecs, np.zeros((1, 0), dtype=np.int64), [{"a": 1}], None)


def test_insert_stream_sends_document_columns_after_the_record_column() -> None:
    pytest.importorskip("grpc_tools")
    from ombra import _proto

    pb, _ = _proto.load_stubs(grpc_module=False)
    blobs = [b"x" * 16384] * 3
    col1 = pir.pack_doc_column(blobs, 1)
    col2 = pir.pack_doc_column([b"y" * 262144], 2)
    block = pb.InsertBlock(api_version=1, collection="c", cluster_id=0, ids=[b"x"], columns=[b"a"])
    block.record_column = b"r" * 100
    block.document_columns.add(doc_class=1, column=col1)
    block.document_columns.add(doc_class=2, column=col2)
    msgs = list(_proto.insert_requests(pb, [block]))
    kinds = [m.WhichOneof("part") for m in msgs]
    assert kinds[:3] == ["header", "column", "record_column"]
    assert set(kinds[3:]) == {"document_column"}
    parts = [m.document_column for m in msgs[3:]]
    assert [p.doc_class for p in parts] == sorted(p.doc_class for p in parts)  # ascending, grouped
    for cls, col in ((1, col1), (2, col2)):
        assert b"".join(p.data for p in parts if p.doc_class == cls) == col
    assert all(len(p.data) <= pb.LIMIT_MAX_CHUNK_BYTES for p in parts)


def test_retrieve_documents_proto_and_packaged_copy() -> None:
    pytest.importorskip("grpc_tools")
    from pathlib import Path

    from ombra import _proto

    pb, _ = _proto.load_stubs(grpc_module=False)
    st = pb.RetrieveDocumentsStart(doc_class=2, m=10)
    assert st.doc_class == 2
    info = pb.RetrieveDocumentsInfo(n_planes=14, part=0, n_parts=1)
    assert info.n_planes == 14
    assert pb.DocumentAnswer(plane=13, bucket=14).plane == 13
    svc = pb.DESCRIPTOR.services_by_name["Ombra"].methods_by_name["RetrieveDocuments"]
    assert svc.client_streaming and svc.server_streaming
    root = Path(__file__).resolve().parents[2]
    assert (root / "proto/ombra/v1/ombra.proto").read_text() == _proto._PROTO.read_text()
