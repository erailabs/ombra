# llama-index-vector-stores-ombra

A LlamaIndex vector store for Ombra. Text, vectors and metadata of each node are encrypted on your
machine; the server only computes on ciphertexts.

```python
from llama_index.vector_stores.ombra import OmbraVectorStore

store = OmbraVectorStore(
    local_path="./ombra-data",  # local mode; or client=Client(...) for a server
    collection_name="docs",
    key_file="docs.key",
    passphrase="...",
)
store.add(nodes)  # nodes must carry embeddings
store.query(VectorStoreQuery(query_embedding=q, similarity_top_k=4))
```

Run `examples/quickstart.py` for a complete example (local mode, no model download).

- **Similarity** is the exact cosine, in [-1, 1], higher is better (not a distance).
- **Dimension** of the embedding must be 384, 768 or 1536.
- **Filters** are `EQ` (one integer) or `IN` (a list of integers) on integer fields declared with
  `filter_fields={"topic": 5}`, combined with AND. Anything else (OR, NOT, nested filters, other
  operators, undeclared fields, non-integer values) raises `ValueError`; nothing is filtered on the
  client.
- **Key file:** back it up. Without it the data is lost.
- **Delete by `ref_doc_id`:** Ombra cannot list the ids of a document, so the store keeps a map from
  each `ref_doc_id` to its node ids. It lives in memory; pass `manifest_path="refs.json"` to keep
  it between runs. The file holds the SHA-256 of each `ref_doc_id` and the node ids (which the
  server sees anyway), no text, metadata or vector. A reopened store without the manifest refuses
  `delete(ref_doc_id)` for documents it did not add; `delete_nodes(node_ids)` always works.
- **Not supported:** query modes other than the default, `node_ids`/`doc_ids` in a query,
  `get_nodes`, `clear`, `delete_nodes` with filters. Adding a node id that is already stored is
  refused and the stored version is kept; to change a node, delete it and add it under a new id.
