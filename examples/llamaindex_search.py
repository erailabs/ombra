# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The same search through LlamaIndex: python llamaindex_search.py"""

import tempfile
from pathlib import Path

from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import VectorStoreQuery
from llama_index.vector_stores.ombra import OmbraVectorStore
from sentence_transformers import SentenceTransformer


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")  # 384 dimensions
    texts = [
        "The cat sat on the warm windowsill.",
        "Interest rates rose again this quarter.",
        "The central bank raised its policy rate.",
    ]
    vectors = model.encode(texts, normalize_embeddings=True).tolist()
    store = OmbraVectorStore(
        local_path=workdir / "server",
        collection_name="notes",
        key_file=workdir / "notes.key",  # holds the secret key: back it up, never share it
        passphrase="change this passphrase",
    )
    store.add([TextNode(text=t, embedding=v) for t, v in zip(texts, vectors, strict=True)])
    question = model.encode(["What did the central bank do?"], normalize_embeddings=True)[0]
    result = store.query(VectorStoreQuery(query_embedding=question.tolist(), similarity_top_k=2))
    for node, score in zip(result.nodes, result.similarities, strict=True):
        print(f"{score:.3f}  {node.get_content()}")
    store.close()
    print(f"Keys and encrypted data are in {workdir}; delete it when you are done.")


if __name__ == "__main__":
    main()
