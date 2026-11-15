# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Unit tests of OmbraVectorStore on ombra.testing.FakeClient (no OpenFHE, nothing encrypted)."""

import hashlib

import numpy as np
import pytest

pytest.importorskip("llama_index.core")

from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode  # noqa: E402
from llama_index.core.vector_stores.types import (  # noqa: E402
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
)
from llama_index.vector_stores.ombra import OmbraVectorStore  # noqa: E402
from ombra.testing import FakeClient  # noqa: E402

SCRYPT = (2**4, 8, 1)  # tiny: the fake client ignores it anyway


def emb(text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
    return np.random.default_rng(seed).standard_normal(384).tolist()


def node(text, node_id=None, ref=None, **meta):
    n = TextNode(text=text, metadata=meta, embedding=emb(text), id_=node_id or text)
    if ref is not None:
        n.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=ref)
    return n


def make(tmp_path, client=None, **kw):
    return OmbraVectorStore(
        client or FakeClient("memory"),
        "docs",
        key_file=tmp_path / "k.key",
        passphrase="correct horse",
        scrypt_params=SCRYPT,
        **kw,
    )


def q(text, k=4, **kw):
    return VectorStoreQuery(query_embedding=emb(text), similarity_top_k=k, **kw)


def eq(key, value):
    return MetadataFilter(key=key, value=value, operator=FilterOperator.EQ)


def test_empty_store_returns_nothing(tmp_path):
    assert make(tmp_path).query(q("x")).nodes == []


def test_add_and_query_with_similarities(tmp_path):
    s = make(tmp_path, filter_fields=None)
    ids = s.add([node("foo", n=1), node("bar", n=2), node("baz", n=3)])
    assert ids == ["foo", "bar", "baz"]
    r = s.query(q("bar", k=3))
    assert r.ids[0] == "bar"
    assert r.nodes[0].get_content() == "bar"
    assert r.nodes[0].metadata == {"n": 2}
    assert r.similarities[0] == pytest.approx(1.0)
    assert r.similarities == sorted(r.similarities, reverse=True)


def test_existing_id_refused_then_delete_by_ref_doc_id(tmp_path):
    s = make(tmp_path)
    s.add([node("a1", ref="docA"), node("a2", ref="docA"), node("b1", ref="docB")])
    with pytest.raises(ValueError):
        s.add([node("a1", ref="docA")])
    s.delete("docA")
    assert s.query(q("a1", k=5)).ids == ["b1"]
    s.delete("never-added")  # a store that knows every node: nothing to do
    s.delete_nodes(["b1", "missing"])
    assert s.query(q("a1", k=5)).nodes == []


def test_node_without_source_is_its_own_ref_doc(tmp_path):
    s = make(tmp_path)
    s.add([node("x"), node("y")])
    s.delete("x")
    assert s.query(q("x", k=5)).ids == ["y"]


def test_reopened_store_refuses_unknown_ref_doc_and_manifest_fixes_it(tmp_path):
    client = FakeClient("memory")
    s = make(tmp_path, client, manifest_path=tmp_path / "m.json")
    s.add([node("a1", ref="docA"), node("b1", ref="docB")])
    text = (tmp_path / "m.json").read_text()
    assert "docA" not in text and "a1" in text  # ref_doc_ids are hashed

    again = make(tmp_path, client, manifest_path=tmp_path / "m.json")
    again.delete("docA")
    assert again.query(q("a1", k=5)).ids == ["b1"]

    bare = make(tmp_path, client)
    with pytest.raises(RuntimeError, match="manifest"):
        bare.delete("docB")
    bare.delete_nodes(["b1"])
    assert bare.query(q("a1", k=5)).nodes == []


def test_filters_eq_and_in(tmp_path):
    s = make(tmp_path, filter_fields={"topic": 3, "lang": 2})
    s.add(
        [
            node("a", topic=0, lang=0),
            node("b", topic=1, lang=0),
            node("c", topic=1, lang=1),
            node("d", topic=2, lang=1),
        ]
    )

    def texts(filters):
        return {n.get_content() for n in s.query(q("a", k=5, filters=filters)).nodes}

    assert texts(MetadataFilters(filters=[eq("topic", 1)])) == {"b", "c"}
    assert texts(MetadataFilters(filters=[eq("topic", 1), eq("lang", 1)])) == {"c"}
    f_in = MetadataFilter(key="topic", value=[0, 2], operator=FilterOperator.IN)
    assert texts(MetadataFilters(filters=[f_in])) == {"a", "d"}


@pytest.mark.parametrize(
    ("filters", "match"),
    [
        (MetadataFilters(filters=[eq("other", 1)]), "declared"),
        (MetadataFilters(filters=[eq("topic", "1")]), "integer"),
        (MetadataFilters(filters=[eq("topic", 1.5)]), "integer"),
        (MetadataFilters(filters=[eq("topic", 1), eq("topic", 2)]), "twice"),
        (
            MetadataFilters(filters=[eq("topic", 1)], condition=FilterCondition.OR),
            "AND only",
        ),
        (
            MetadataFilters(filters=[MetadataFilter(key="topic", value=1, operator=FilterOperator.GT)]),
            "not supported",
        ),
        (
            MetadataFilters(
                filters=[MetadataFilter(key="topic", value=["a"], operator=FilterOperator.IN)]
            ),
            "integers",
        ),
        (MetadataFilters(filters=[MetadataFilters(filters=[eq("topic", 1)])]), "nested"),
    ],
)
def test_unsupported_filters_raise(tmp_path, filters, match):
    s = make(tmp_path, filter_fields={"topic": 3})
    s.add([node("a", topic=0)])
    with pytest.raises(ValueError, match=match):
        s.query(q("a", filters=filters))


def test_filters_are_checked_even_on_an_empty_store(tmp_path):
    with pytest.raises(ValueError, match="declared"):
        make(tmp_path).query(q("a", filters=MetadataFilters(filters=[eq("t", 1)])))


def test_unsupported_query_shapes(tmp_path):
    s = make(tmp_path)
    s.add([node("a")])
    with pytest.raises(ValueError, match="mode"):
        s.query(q("a", mode=VectorStoreQueryMode.HYBRID))
    with pytest.raises(ValueError, match="node_ids"):
        s.query(q("a", node_ids=["a"]))
    with pytest.raises(ValueError, match="query_embedding"):
        s.query(VectorStoreQuery(query_str="a", similarity_top_k=1))
    with pytest.raises(ValueError, match="filters"):
        s.delete_nodes(["a"], filters=MetadataFilters(filters=[eq("t", 1)]))


def test_missing_declared_filter_field_is_refused(tmp_path):
    s = make(tmp_path, filter_fields={"topic": 3})
    with pytest.raises(ValueError, match="topic"):
        s.add([node("a")])


def test_repr_and_passphrase_are_not_kept(tmp_path):
    s = make(tmp_path)
    assert "correct horse" not in repr(s)
    s.add([node("a")])
    assert s._passphrase is None


def test_existing_key_file_is_never_overwritten(tmp_path):
    (tmp_path / "k.key").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        make(tmp_path).add([node("a")])


def test_failed_create_removes_the_key_file(tmp_path):
    s = make(tmp_path)
    real = s._client.create_collection

    def failing(*a, **kw):
        kw["key_file"].write_bytes(b"half written key file")
        raise RuntimeError("server refused")

    s._client.create_collection = failing
    with pytest.raises(RuntimeError, match="server refused"):
        s.add([node("a")])
    assert not (tmp_path / "k.key").exists()
    s._client.create_collection = real
    s.add([node("a")])
