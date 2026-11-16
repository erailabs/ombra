# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The quickstart example, and a real round trip with deletes, in local mode (needs OpenFHE)."""

import importlib.util
import shutil
from pathlib import Path

import pytest

pytest.importorskip("llama_index.core")
pytest.importorskip("openfhe")

pytestmark = pytest.mark.openfhe

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "quickstart.py"


def load_example():
    spec = importlib.util.spec_from_file_location("li_quickstart", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quickstart_runs(tmp_path):
    module = load_example()
    try:
        found = module.main(tmp_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)  # local mode data is large
    assert [text for text, _ in found] == ["gpu kernels"]  # the only node with topic 1
    assert found[0][1] == pytest.approx(1.0, abs=1e-3)


def test_round_trip_delete_by_ref_doc_id(tmp_path):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
    from llama_index.core.vector_stores.types import VectorStoreQuery
    from llama_index.vector_stores.ombra import OmbraVectorStore

    module = load_example()
    store = OmbraVectorStore(
        local_path=tmp_path / "server",
        collection_name="rt",
        key_file=tmp_path / "rt.key",
        passphrase="pw",
    )
    try:
        nodes = []
        for text, ref in [("one", "docA"), ("two", "docA"), ("three", "docB")]:
            n = TextNode(text=text, embedding=module.embed(text), id_=text)
            n.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=ref)
            nodes.append(n)
        store.add(nodes)
        q = VectorStoreQuery(query_embedding=module.embed("three"), similarity_top_k=3)
        assert store.query(q).nodes[0].get_content() == "three"
        store.delete("docA")
        assert [n.get_content() for n in store.query(q).nodes] == ["three"]
    finally:
        store.close()
        shutil.rmtree(tmp_path, ignore_errors=True)
