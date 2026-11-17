# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""End to end in local mode: encrypt, store and search a few nodes with LlamaIndex.

    python quickstart.py [DIRECTORY]

Needs `pip install "ombra[local]" llama-index-vector-stores-ombra`. Everything goes under
DIRECTORY (a temporary directory by default). The embeddings are deterministic pseudo-random
vectors, so no model is downloaded; they are not meaningful, so only an exact repeat of a text
ranks it first. Use a real model in your own code (its dimension must be 384, 768 or 1536).
Local mode takes a few seconds per search.
"""

import hashlib
import sys
import tempfile
from pathlib import Path

import numpy as np
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
)
from llama_index.vector_stores.ombra import OmbraVectorStore


def embed(text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
    return np.random.default_rng(seed).standard_normal(384).tolist()


def main(directory: Path) -> list[tuple[str, float]]:
    store = OmbraVectorStore(
        local_path=directory / "server",
        collection_name="quickstart",
        key_file=directory / "quickstart.key",  # back this file up: it holds the secret key
        passphrase="change me",
        filter_fields={"topic": 2},  # integer metadata you can filter on
    )
    try:
        rows = [("encrypted search", 0), ("gpu kernels", 1), ("vector databases", 0)]
        nodes = [
            TextNode(text=t, metadata={"topic": topic}, embedding=embed(t)) for t, topic in rows
        ]
        store.add(nodes)
        # Similarity = cosine, higher is better.
        result = store.query(
            VectorStoreQuery(
                query_embedding=embed("gpu kernels"),
                similarity_top_k=2,
                filters=MetadataFilters(
                    filters=[MetadataFilter(key="topic", value=1, operator=FilterOperator.EQ)]
                ),
            )
        )
        found = [
            (n.get_content(), s) for n, s in zip(result.nodes, result.similarities, strict=True)
        ]
        for text, score in found:
            print(f"{score:.3f}  {text}")
        return found
    finally:
        store.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
