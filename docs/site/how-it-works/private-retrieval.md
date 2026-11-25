# Private record retrieval

Re-ranking needs the exact vector of each of the best candidates. By default the client reads those records as blobs by id, and the server sees which ids were asked for. Private record retrieval removes that: the client fetches the same rows with one encrypted call, and the server cannot tell which rows it handed over.

It is off by default. Turn it on when you create the collection:

```python
client.create_collection(
    "docs", vectors, ids, key_file="docs.key", passphrase="...", private_retrieval=True
)
```

## What changes

- **At insert.** Next to each block of vectors the client stores a record column: one sealed row per vector, holding the exact float32 vector (and, on a collection with range fields, the integers that the exact range check needs). A row is encrypted with AES-256-GCM under a key derived from your master key. Its associated data binds the collection, the cluster, the id and a version, so a row that is moved, stale or damaged fails its tag. The client drops such a row and counts it. Repacking a block rewrites its record column.
- **At search.** After the last round the client makes one `RetrieveRecords` call instead of one blob request per candidate. It asks for `rerank_factor * k` rows (times 4 on a collection with range fields), the same number for every query. If fewer candidates exist, the rest of the rows are random real rows. Exact scores come from the decrypted rows.
- **Keys.** The client makes a second key pair for this, kept in the same passphrase-protected key file (the file becomes version 2 only when this pair exists). The public evaluation keys, about 41 MB, are uploaded once per collection. If the server has none, or other ones, the client uploads them once and repeats the call.

## What it hides

The server sees the scope of the call: the clusters the search requested (decoys included at Fast and Decoy, all clusters at Strict), the number of rows asked for, and the sizes of the messages. It does not see which rows. At Strict, two different queries produce the same view: same sizes, same scope, same number of messages. The ids of the candidates are no longer revealed by the re-rank.

## What it does not hide

- **Hits carry no metadata.** Reading the metadata or the document of the final k means fetching their blobs by id, which would show those ids. A search on a private-retrieval collection therefore returns hits with empty metadata. Reading a record or document by id is an explicit call and it does reveal that id to the server.
- **Insert still shows cluster membership.** Each insert sends the cluster id next to the vector id, as in every mode.
- **Cluster choice at Fast and Decoy.** The requested clusters are visible as before. See [What the server learns](../security/what-the-server-learns.md).
- **Counts and timing.** The number of rows, the call and its duration.

## What it costs

- **Download.** About 132 KB per bucket, and a call has 1.5 buckets per row asked for. With `k = 10` and the default factor 3, m is 30 and the answer is about 6 MB per search. A collection with range fields asks for 4 times as many.
- **Upload.** About 41 MB of public keys once per collection, and the query ciphertexts of each call.
- **Server work.** The server computes over every row in scope, so a Strict search is the most expensive. On a GPU server, the first and largest step (each query ciphertext times every stored row) runs on the GPU, and the remaining steps run on the CPU. The GPU gives the same answer bytes as the CPU, which tests check for each build. A server built without CUDA, or running on a machine without a usable GPU, answers entirely on the CPU.
- **Storage.** One row of about 3 KB per vector at 768 dimensions, next to the vector's ciphertext columns.
- **Through a router.** When the scope spans several nodes the call is made in parts, one per node, each with the full row count. The router splits the scope and forwards the encrypted queries and answers unchanged, so it learns no more than a node does. The cost grows with the number of parts.

A candidate that does not fit the batch placement (a cuckoo failure) is dropped and counted, never retried: a retry would reveal that a first attempt failed on specific rows. `SearchResult` reports `pir_dropped`, `pir_stale` and `pir_bad_rows`.

Private retrieval is available in local mode too, answered by a CPU reference.
