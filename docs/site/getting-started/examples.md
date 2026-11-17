# Examples

The scripts live in the `examples/` folder of the repository. Each one is a complete program. The local ones need `pip install "ombra[local]" sentence-transformers`; see [Install](install.md).

| Script | What it shows | Needs |
| --- | --- | --- |
| `examples/local_search.py` | create a collection and search it; this is the [quickstart](quickstart.md) | local mode |
| `examples/filters_and_updates.py` | filters, insert, delete and reopening a collection; see [your first search](first-search.md) | local mode |
| `examples/langchain_search.py` | the same search through LangChain, below | local mode, `langchain-ombra` |
| `examples/llamaindex_search.py` | the same search through LlamaIndex, below | local mode, `llama-index-vector-stores-ombra` |
| `examples/server_search.py` | the quickstart against a server; see [Local mode or a server](local-or-server.md) | a running server |

Each local script creates its data in a temporary folder and prints the path. Delete it when you are done.

## filters_and_updates.py

It creates a collection of six texts with a `topic` field, then runs five searches: none filtered, one filtered to a topic, one after an insert, one after a delete, and one after reopening the collection in a new client. It prints each result with its topic, so you can check that a filter only lets matching vectors through.

```bash
python examples/filters_and_updates.py
```

Each search takes several seconds in local mode, so the run takes about a minute.

## langchain_search.py

`langchain-ombra` is a LangChain `VectorStore` that encrypts texts, vectors and metadata on your machine. Install it with `pip install langchain-ombra`. The example wraps the same embedding model in LangChain's `Embeddings` class:

```python
"""The same search through LangChain: python langchain_search.py"""

import tempfile
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_ombra import OmbraVectorStore
from ombra import Client
from sentence_transformers import SentenceTransformer


class MiniLM(Embeddings):
    """all-MiniLM-L6-v2 (384 dimensions) as a LangChain embedding; vectors are unit length."""

    def __init__(self) -> None:
        self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    store = OmbraVectorStore.from_texts(
        [
            "The cat sat on the warm windowsill.",
            "Interest rates rose again this quarter.",
            "The central bank raised its policy rate.",
        ],
        MiniLM(),
        client=Client.local(workdir / "server"),
        collection_name="notes",
        key_file=workdir / "notes.key",  # holds the secret key: back it up, never share it
        passphrase="change this passphrase",
    )
    for doc, score in store.similarity_search_with_score("What did the central bank do?", k=2):
        print(f"{score:.3f}  {doc.page_content}")
    store.close()
    print(f"Keys and encrypted data are in {workdir}; delete it when you are done.")


if __name__ == "__main__":
    main()
```

To use a server, pass a `Client(...)` for a server instead of `Client.local(...)`. Filters, scores and what the adapter does not support are in the [langchain-ombra README](https://github.com/erailabs/ombra/blob/main/integrations/langchain-ombra/README.md).

## llamaindex_search.py

`llama-index-vector-stores-ombra` is a LlamaIndex vector store that encrypts the text, vector and metadata of each node on your machine. Install it with `pip install llama-index-vector-stores-ombra`. The example embeds three texts with the same model and queries the store directly:

```python
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
```

To use a server, pass `client=Client(...)` for a server instead of `local_path`. Filters, scores, deleting by `ref_doc_id` and what the adapter does not support are in the [llama-index-vector-stores-ombra README](https://github.com/erailabs/ombra/blob/main/integrations/llama-index-vector-stores-ombra/README.md).
