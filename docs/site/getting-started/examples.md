# Examples

The scripts are in the `examples/` folder of the repository. The local ones need `pip install "ombra[local]" sentence-transformers`, see [Install](install.md). Each creates its data in a temporary folder and prints the path; delete it when done.

| Script | What it shows | Needs |
| --- | --- | --- |
| `examples/local_search.py` | create a collection and search it, the [quickstart](quickstart.md) | local mode |
| `examples/filters_and_updates.py` | filters, insert, delete and reopening, see [your first search](first-search.md) | local mode |
| `examples/langchain_search.py` | the same search through LangChain | local mode, `langchain-ombra` |
| `examples/llamaindex_search.py` | the same search through LlamaIndex | local mode, `llama-index-vector-stores-ombra` |
| `examples/server_search.py` | the quickstart against a server, see [Local mode or a server](local-or-server.md) | a running server |

## LangChain

`langchain-ombra` (`pip install langchain-ombra`) is a LangChain `VectorStore` that encrypts texts, vectors and metadata on your machine. You wrap your embedding model in LangChain's `Embeddings` class and pass a client:

```python
store = OmbraVectorStore.from_texts(
    texts,
    MiniLM(),  # your Embeddings subclass; vectors must be unit length
    client=Client.local(workdir / "server"),
    collection_name="notes",
    key_file=workdir / "notes.key",  # holds the secret key: back it up, never share it
    passphrase="change this passphrase",
)
for doc, score in store.similarity_search_with_score("What did the central bank do?", k=2):
    print(f"{score:.3f}  {doc.page_content}")
```

To use a server, pass a `Client(...)` for a server instead of `Client.local(...)`. Filters, scores and unsupported features are in the [langchain-ombra README](https://github.com/erailabs/ombra/blob/main/integrations/langchain-ombra/README.md).

## LlamaIndex

`llama-index-vector-stores-ombra` (`pip install llama-index-vector-stores-ombra`) is a LlamaIndex vector store that encrypts the text, vector and metadata of each node on your machine:

```python
store = OmbraVectorStore(
    local_path=workdir / "server",
    collection_name="notes",
    key_file=workdir / "notes.key",
    passphrase="change this passphrase",
)
store.add([TextNode(text=t, embedding=v) for t, v in zip(texts, vectors, strict=True)])
result = store.query(VectorStoreQuery(query_embedding=question, similarity_top_k=2))
```

To use a server, pass `client=Client(...)` instead of `local_path`. Filters, scores, deleting by `ref_doc_id` and unsupported features are in the [llama-index-vector-stores-ombra README](https://github.com/erailabs/ombra/blob/main/integrations/llama-index-vector-stores-ombra/README.md).
