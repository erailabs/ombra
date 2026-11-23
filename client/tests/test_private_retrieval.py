# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private record retrieval, client parts that need no OpenFHE: key file version, record rows and
columns, the Insert stream. Synthetic data only."""

from __future__ import annotations

import os

import numpy as np
import pytest
from ombra import keyfile, pir
from ombra import private_retrieval as pr
from ombra.keyfile import KeyFileError, KeyMaterial
from ombra.ranges import RangeSchema

FAST = (2**4, 8, 1)
PASS = "correct horse battery staple"  # noqa: S105


def test_key_file_without_retrieval_key_stays_version_1() -> None:
    m = KeyMaterial.generate(os.urandom(300))
    blob = keyfile.export_key_bytes(m, PASS, scrypt_params=FAST)
    assert blob[4] == 1  # u16 version, little-endian
    got = keyfile.import_key_bytes(blob, PASS)
    assert got == m and got.pir_key == b""


def test_key_file_with_retrieval_key_is_version_2_and_round_trips() -> None:
    m = KeyMaterial(os.urandom(300), os.urandom(32), os.urandom(500))
    blob = keyfile.export_key_bytes(m, PASS, scrypt_params=FAST)
    assert blob[4] == 2
    assert keyfile.import_key_bytes(blob, PASS) == m
    assert "pir_key=<500 bytes>" in repr(m)
    assert m.pir_key.hex() not in repr(m)
    for i in range(len(blob) - 1, len(blob) - 40, -7):  # a changed byte is refused
        bad = bytearray(blob)
        bad[i] ^= 1
        with pytest.raises(KeyFileError):
            keyfile.import_key_bytes(bytes(bad), PASS)
    with pytest.raises(KeyFileError):
        keyfile.import_key_bytes(blob, "wrong passphrase")


def test_rows_of_a_block_open_only_where_they_belong() -> None:
    codec = pir.RowCodec(bytes(range(32)), "coll", 6, 0)
    ids = [b"id-%02d-synthetic" % i for i in range(4)]
    vecs = np.random.default_rng(1).standard_normal((4, 6)).astype("<f4")
    col = pir.parse_column(pr.seal_column(codec, 3, ids, vecs, np.zeros((4, 0), dtype=np.int64)))
    assert col.n_rows == 4 and col.row_size == codec.row_size
    for i, ident in enumerate(ids):
        v, ints = codec.open(col.row(i), 3, ident, pr.ROW_VERSION)
        assert np.array_equal(v, vecs[i]) and len(ints) == 0
        with pytest.raises(pir.PirError):  # another cluster, id or version fails the tag
            codec.open(col.row(i), 4, ident, pr.ROW_VERSION)
        with pytest.raises(pir.PirError):
            codec.open(col.row(i), 3, ids[(i + 1) % 4], pr.ROW_VERSION)
        with pytest.raises(pir.PirError):
            codec.open(col.row(i), 3, ident, pr.ROW_VERSION + 1)


def test_integers_of_a_row_hold_filter_values_then_raw_range_values() -> None:
    schema = RangeSchema.from_spec({"year": [2000, 2010], "price": {"kind": "two_digit"}}, 1, ["g"])
    assert pr.n_ints_for(4, schema) == 6 and pr.n_ints_for(4, None) == 0
    meta = [{"year": 2005, "price": 130}, {"year": 1999, "price": 4000}]
    fields = np.hstack([np.array([[1], [2]]), schema.bucket_values(meta, 2)])
    ints = pr.exact_ints(fields, meta, schema)
    assert ints.shape == (2, 6)
    assert ints[0, :4].tolist() == fields[0].tolist()
    assert ints[0, 4:].tolist() == [2005, 130] and ints[1, 4:].tolist() == [1999, 4000]
    stored = {**meta[0], "\x00ombra/fields": fields[0].tolist()}
    assert pr.ints_from_meta(stored, schema, 4) == ints[0].tolist()
    with pytest.raises(ValueError):
        pr.ints_from_meta(meta[0], schema, 4)  # a record without its filter values
    assert pr.exact_ints(fields, None, None).shape == (2, 0)


def test_insert_stream_sends_the_record_column_in_parts_after_the_columns() -> None:
    pytest.importorskip("grpc_tools")
    from ombra import _proto

    pb, _ = _proto.load_stubs(grpc_module=False)
    big = os.urandom(_proto.RECORD_PART * 2 + 5)
    block = pb.InsertBlock(api_version=1, collection="c", cluster_id=0, ids=[b"x"], columns=[b"a"])
    block.record_column = big
    msgs = list(_proto.insert_requests(pb, [block]))
    kinds = [m.WhichOneof("part") for m in msgs]
    assert kinds == ["header", "column", "record_column", "record_column", "record_column"]
    assert b"".join(m.record_column for m in msgs[2:]) == big
    assert all(len(m.record_column) <= pb.LIMIT_MAX_CHUNK_BYTES for m in msgs[2:])
    plain = pb.InsertBlock(api_version=1, collection="c", cluster_id=0, ids=[b"x"], columns=[b"a"])
    assert [m.WhichOneof("part") for m in _proto.insert_requests(pb, [plain])] == [
        "header",
        "column",
    ]


def test_retrieve_proto_fields_exist() -> None:
    pytest.importorskip("grpc_tools")
    from ombra import _proto

    pb, _ = _proto.load_stubs(grpc_module=False)
    assert pb.RetrieveRecordsInfo(part=1, n_parts=2).n_parts == 2
    assert pb.RecordAnswer(part=1).part == 1
