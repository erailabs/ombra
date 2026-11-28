# Private record retrieval

Re-ranking needs the exact vector of each of the best candidates. By default the client reads those records as blobs by id, and the server sees which ids were asked for. Private record retrieval removes that: the client fetches the same rows with one encrypted call, and the server cannot tell which rows it handed over. It is off by default:

```python
client.create_collection(
    "docs", vectors, ids, key_file="docs.key", passphrase="...", private_retrieval=True
)
```

## What changes

- **At insert.** Next to each block the client stores a record column: one sealed row per vector (format in [documents and blobs](documents.md)). Repacking a block rewrites its record column. A moved, stale or damaged row fails its tag; the client drops and counts it.
- **At search.** After the last round the client makes one `RetrieveRecords` call instead of one blob request per candidate. It asks for `rerank_factor * k` rows (times 4 with range fields), the same number for every query. If fewer candidates exist, the rest are random real rows. Exact scores come from the decrypted rows.
- **Keys.** The client makes a second key pair, kept in the same passphrase-protected key file (the file becomes version 2 only when this pair exists). The public evaluation keys, about 41 MB, are uploaded once per collection. If the server has none, or other ones, the client uploads them once and repeats the call.

## Metadata and documents

The record row has a metadata slot (`metadata_bytes`, 512 by default), so the hits of a search come back with their metadata and no extra call. With `fetch_documents=True` the client also fetches the documents of the final hits that are in the 16 KiB or 256 KiB class, with one `RetrieveDocuments` call per class and always `k` rows per call, so the server cannot tell which rows or how many of them were hits. Documents of both classes are stored as columns next to the block, the same sealed blobs as by id ([documents and blobs](documents.md#private-fetch-of-documents)). Metadata too large for the slot is refused at insert.

```python
hits = client.search("docs", query, k=10, fetch_documents=True)
hits[0].metadata, hits[0].document, hits[0].document_status
```

Collections made before this keep working: their rows have no metadata slot, hits carry empty metadata, and `GetBlob` reads as before.

## What it hides

The server sees the scope of the call: the clusters the search requested (decoys included at Fast and Decoy, all clusters at Strict), the number of rows asked for, and the message sizes. It does not see which rows. At Strict, two different queries produce the same view. The ids of the candidates are no longer revealed by the re-rank, and, with `fetch_documents=True`, neither are the ids of the hits whose documents are fetched.

## What it does not hide

- **Reading by id.** `get_document` and the blob calls still take an id and reveal it. Use them only when you accept that.
- **Large documents.** A document over 256 KiB (the 2 MiB and 16 MiB classes) is not fetched privately: the hit reports it as large, and reading it by id shows that id.
- **Insert still shows cluster membership**, as in every mode.
- **Cluster choice at Fast and Decoy** is visible as before ([What the server learns](../security/what-the-server-learns.md)).
- **Counts and timing:** the number of rows, the call and its duration.
- **Sizes of the document classes.** The server sees that there are documents in the 16 KiB or 256 KiB class (every search makes a call per class; for a class the collection has no document of, the call ends after the server says so), the number of rows of each class on each node, and the number of planes of a class (1 for 16 KiB, 14 for 256 KiB). It does not see which vector has which document.

## What it costs

- **Download.** About 132 KB per bucket, and a call has 1.5 buckets per row asked for. With `k = 10` and factor 3, that is 30 rows and about 6 MB per search. A collection with range fields asks for 4 times as many.
- **Upload.** About 41 MB of public keys once per collection, plus the query ciphertexts of each call.
- **Server work.** The server computes over every row in scope, so Strict is the most expensive. On a GPU server two steps run on the GPU: the products of the query with every stored row, and the second-dimension product. Query expansion, compression and serialization run on the CPU. Before the second-dimension product moved, it took about 58% of a call at 10,000 rows and query expansion about 37% (CPU shares, 15 buckets; compression and serialization about 0.1%). The answer bytes are the same as the CPU's. A server built without CUDA, or without a usable GPU, answers on the CPU. Measured times: TBD, see [Performance](../performance/index.md).
- **Document fetch.** One call per class on top of the search, each with the upload of the query ciphertexts and a download of `ceil(class size / 19,456)` planes of answers. Measured on a small collection (Fast preset, 384 dimensions, `k = 10`, Strict, keys already uploaded):

    | Call | Upload | Download | Messages up / down |
    | --- | --- | --- | --- |
    | Search rounds | 0.39 MB | 1.3 MB | 3 / 5 |
    | Records (30 rows) | 0.26 MB | 5.9 MB | 2 / 46 |
    | Documents, 16 KiB class | 0.26 MB | 2.0 MB | 2 / 16 |
    | Documents, 256 KiB class | 0.26 MB | 27.7 MB | 2 / 211 |

    Server time at 100,000 and 1,000,000 blobs: not measured yet. Measured times: TBD, see [Performance](../performance/index.md).
- **Storage.** One row of about 3 KB per vector at 768 dimensions.
- **Through a router.** When the scope spans several nodes the call is made in parts, one per node, each with the full row count. The router forwards the encrypted queries and answers unchanged, so it learns no more than a node does.

A candidate that does not fit the batch placement (a cuckoo failure) is dropped and counted, never retried, because a retry would reveal that a first attempt failed on specific rows. `SearchResult` reports `pir_dropped`, `pir_stale` and `pir_bad_rows`. Private retrieval is available in local mode too, answered by a CPU reference.
