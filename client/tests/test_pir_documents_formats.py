# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private fetch of metadata and documents: row v2, document columns, planes. No OpenFHE needed
(the HE tests are in test_pir_documents_openfhe.py). Synthetic data only."""

from __future__ import annotations

import numpy as np
import pytest
from ombra import pir
from ombra.pir import PirError

KEY = bytes(range(32))
SEED = b"0123456789abcdef"


def _vec(dim: int, k: int = 0) -> np.ndarray:
    return np.random.default_rng(k).standard_normal(dim).astype("<f4")


def codec2(dim: int = 16, n_ints: int = 2, meta: int = 128) -> pir.RowCodec:
    return pir.RowCodec(KEY, "coll", dim, n_ints, meta)


# ---------------------------------------------------------------- row v2


def test_row_v2_round_trip_with_metadata_and_document_pointer() -> None:
    c = codec2()
    v = _vec(16)
    meta = {"title": "synthetic é", "n": 3, "tags": ["a", "b"]}
    row = c.seal(2, b"rec-1", 1, v, [5, -6], meta, pir.DOC_CLASS_256K, 77)
    assert len(row) == c.row_size == pir.row_size_for(16, 2, 128)
    got = c.open_row(row, 2, b"rec-1", 1)
    assert np.array_equal(got.vector, v) and got.ints.tolist() == [5, -6]
    assert got.metadata == meta and (got.doc_class, got.doc_index) == (2, 77)
    vec, ints = c.open(row, 2, b"rec-1", 1)  # the two-tuple view the v1 callers use
    assert np.array_equal(vec, v) and ints.tolist() == [5, -6]


def test_row_v2_layout_is_the_documented_plaintext() -> None:
    """vector || ints || u16 length || compact JSON padded || u8 class || u32 index, under the AAD
    domain ombra.pir.row.v2."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    c = codec2(4, 1, 64)
    v = np.arange(4, dtype="<f4")
    row = c.seal(7, b"id", 1, v, [9], {"b": 1, "a": [1, 2]}, 1, 5)
    key = HKDF(hashes.SHA256(), 32, None, b"ombra pir row key v1").derive(KEY)
    aad = (
        b"ombra.pir.row.v2"
        + (4).to_bytes(4, "little")
        + b"coll"
        + (7).to_bytes(4, "little")
        + (2).to_bytes(4, "little")
        + b"id"
        + (1).to_bytes(4, "little")
    )
    sealed_len = 12 + 16 + 8 + 2 + 64 + 5 + 16
    plain = AESGCM(key).decrypt(row[:12], row[12:sealed_len], aad)
    js = b'{"a":[1,2],"b":1}'
    want = v.tobytes() + (9).to_bytes(8, "little") + len(js).to_bytes(2, "little")
    want += js.ljust(64, b"\0") + bytes([1]) + (5).to_bytes(4, "little")
    assert plain == want
    assert len(row) % 64 == 0 and not any(row[sealed_len:])


def test_v1_and_v2_rows_do_not_open_each_other() -> None:
    v = _vec(16)
    r1 = pir.RowCodec(KEY, "coll", 16, 2).seal(0, b"x", 1, v, [1, 2])
    with pytest.raises(PirError):
        codec2().open_row(r1, 0, b"x", 1)
    # same row size on purpose: only the AAD domain tells them apart
    a = pir.RowCodec(KEY, "coll", 16, 2, 0)  # v2 with no metadata slot
    b = pir.RowCodec(KEY, "coll", 16, 2)  # v1
    assert a.row_size == b.row_size
    with pytest.raises(PirError):
        b.open_row(a.seal(0, b"x", 1, v, [1, 2]), 0, b"x", 1)
    with pytest.raises(PirError):
        a.open_row(b.seal(0, b"x", 1, v, [1, 2]), 0, b"x", 1)


def test_v1_rows_still_open_and_give_empty_metadata() -> None:
    c = pir.RowCodec(KEY, "coll", 16, 2)  # no metadata slot: the v1 format
    assert c.format == 1
    row = c.seal(1, b"old", 1, _vec(16), [3, 4])
    got = c.open_row(row, 1, b"old", 1)
    assert got.metadata == {} and (got.doc_class, got.doc_index) == (0, 0)
    with pytest.raises(PirError, match="v1"):
        c.seal(1, b"old", 1, _vec(16), [3, 4], {"a": 1})


def test_row_v2_rejects_metadata_over_the_limit_and_names_it() -> None:
    c = codec2(meta=16)
    c.seal(0, b"x", 1, _vec(16), [0, 0], {"k": "01234567"})  # exactly 16 bytes of JSON
    with pytest.raises(PirError, match="limit of 16 bytes"):
        c.seal(0, b"x", 1, _vec(16), [0, 0], {"k": "012345678"})


def test_row_v2_pointer_rules_and_tampering() -> None:
    c = codec2()
    for cls, idx in [(0, 1), (3, 1), (4, 0)]:
        with pytest.raises(PirError):
            c.seal(0, b"x", 1, _vec(16), [0, 0], None, cls, idx)
    row = c.seal(0, b"x", 1, _vec(16), [0, 0], {"a": 1}, 1, 2)
    for i in (20, 70, 150):
        bad = bytearray(row)
        bad[i] ^= 1
        with pytest.raises(PirError):
            c.open_row(bytes(bad), 0, b"x", 1)
    with pytest.raises(PirError):
        c.open_row(row, 1, b"x", 1)
    with pytest.raises(PirError):
        c.open_row(row, 0, b"y", 1)


def test_max_metadata_bytes_from_the_layout() -> None:
    # 19,456 - (12 + 4 d + 8 n + 7 + 16), rounded down to 64
    assert pir.max_metadata_bytes(768, 0) == 16320
    assert pir.max_metadata_bytes(384, 0) == 17856
    assert pir.max_metadata_bytes(768, 4) == 16256
    for d, n in [(1, 0), (384, 3), (768, 0), (1536, 5)]:
        m = pir.max_metadata_bytes(d, n)
        assert m % 64 == 0 and pir.row_size_for(d, n, m) <= 19456
        assert pir.row_size_for(d, n, m + 64) > 19456  # the next step of 64 would not fit
        pir.RowCodec(KEY, "c", d, n, m)  # the largest slot is accepted
        with pytest.raises(PirError):
            pir.RowCodec(KEY, "c", d, n, m + 64)
    with pytest.raises(PirError):
        pir.max_metadata_bytes(5000, 0)


def test_metadata_json_is_compact_and_sorted() -> None:
    assert pir.metadata_json({"b": 1, "a": "x"}) == b'{"a":"x","b":1}'
    assert pir.metadata_json(None) == b"{}"


# ---------------------------------------------------------------- columns


def test_column_versions_1_and_2_are_both_read() -> None:
    c = pir.RowCodec(KEY, "coll", 16, 1)
    rows = [c.seal(0, b"id%d" % i, 1, _vec(16, i), [i]) for i in range(3)]
    v1 = pir.pack_column(rows, c.row_size, version=1)
    v2 = pir.pack_column(rows, c.row_size)
    assert pir.parse_column(v1).version == 1 and pir.parse_column(v2).version == 2
    assert pir.parse_column(v1).body == pir.parse_column(v2).body
    with pytest.raises(PirError):
        pir.pack_column(rows, c.row_size, version=3)


def test_document_column_round_trip_and_checks() -> None:
    blobs = [bytes([i]) * 16384 for i in range(3)]
    data = pir.pack_doc_column(blobs, 1)
    assert data[:4] == pir.DOC_COLUMN_MAGIC
    col = pir.parse_doc_column(data)
    assert (col.row_size, col.n_rows, col.doc_class, col.version) == (16384, 3, 1, 1)
    assert [col.row(i) for i in range(3)] == blobs
    assert pir.parse_doc_column(pir.pack_doc_column([], 2)).row_size == 262144
    with pytest.raises(PirError):
        pir.pack_doc_column([b"x" * 100], 1)
    with pytest.raises(PirError):
        pir.pack_doc_column([], 3)  # large documents have no column
    with pytest.raises(PirError, match="not a document column"):
        pir.parse_doc_column(pir.pack_column([bytes(64)], 64))
    with pytest.raises(PirError, match="version"):
        pir.parse_doc_column(data[:4] + (9).to_bytes(2, "little") + data[6:])
    with pytest.raises(PirError, match="malformed"):
        pir.parse_doc_column(data[:6] + (2).to_bytes(2, "little") + data[8:])  # class vs size
    with pytest.raises(PirError, match="length"):
        pir.parse_doc_column(data[:-1])
    with pytest.raises(PirError, match="truncated"):
        pir.parse_doc_column(data[:10])
    with pytest.raises(PirError):  # a document column is not a record column
        pir.parse_column(data)


def test_doc_class_of_blob_size() -> None:
    assert pir.doc_class_of(None) == 0
    assert pir.doc_class_of(16384) == 1 and pir.doc_class_of(262144) == 2
    assert pir.doc_class_of(2097152) == 3 and pir.doc_class_of(16777216) == 3


# ---------------------------------------------------------------- planes


def test_plane_layout_of_the_classes() -> None:
    assert pir.plane_layout(16384) == (1, 16384)
    assert pir.plane_layout(262144) == (14, 19456)  # 13.47 planes: the last is zero padded
    assert pir.plane_layout(2097152)[0] == 108 and pir.plane_layout(16777216)[0] == 863


def test_plane_chunks_cover_the_row_and_pad_the_last() -> None:
    row = bytes(range(256)) * 1024  # 262,144 bytes
    chunks = [pir.plane_chunk(row, p, 262144) for p in range(14)]
    assert all(len(c) == 19456 for c in chunks)
    assert b"".join(chunks)[:262144] == row
    assert not any(chunks[-1][262144 - 13 * 19456 :])  # the zero padding of the last plane
    assert pir.plane_chunk(row[:16384], 0, 16384) == row[:16384]
    with pytest.raises(PirError):
        pir.plane_chunk(row, 14, 262144)
    with pytest.raises(PirError):
        pir.plane_chunk(row[:-1], 0, 262144)


def test_doc_shape_shares_one_shape_across_planes() -> None:
    d16 = pir.make_doc_shape(50, 10, 16384, SEED)
    d256 = pir.make_doc_shape(50, 10, 262144, SEED)
    assert (d16.planes, d256.planes) == (1, 14)
    assert d16.shape.buckets == d256.shape.buckets == 15
    assert d256.n_answers == 14 * 15
    assert pir.make_doc_shape(0, 10, 16384, SEED).shape.n_rows == 1  # the padding row
