# Retrieve

Private fetch of the re-rank rows of a client's candidates, so that the server does not learn which rows. The rows are the [record columns](insert-and-delete.md#record-column) stored with the blocks. `Error` is on the [Messages](messages.md) page.

The server answers on OpenFHE (BFV, preset `pir_bfv_n8192`), on the CPU. The answers decrypt, on the client, to the rows of the database that the client asked for; the server does not learn which.

## RetrieveRecords

```proto
rpc RetrieveRecords(stream RetrieveRecordsRequest) returns (stream RetrieveRecordsResponse)
```

Scope: `read`.

The database of one call is the concatenation of the record columns of the blocks in scope. The scope is the listed data clusters, or all data clusters. The blocks of a cluster come in the order they were created, and the clusters in ascending id order. Row i of the database is the i-th row of that concatenation. Center blocks hold no records and are never in scope.

The stream is bidirectional, and its order matters, because the shape of a call (and so the number of queries) depends on the number of rows in scope:

1. The client sends one `RetrieveRecordsStart`.
2. The server checks the start, the keys and the scope, and answers with one `RetrieveRecordsInfo`, or with an `Error` and the end. The client builds its queries from the Info: it knows `n_rows` and the block layout, and the row index of a candidate is `span.first_row` plus its slot in the block.
3. The client sends exactly as many messages with a `query` as the shape needs (`shape.n_cts`, computed from the Info the same way the server does). Then it closes its side of the stream (a half-close): the server computes the answers only after that. An extra query is `ERROR_BAD_SEQUENCE`, and so is a stream that ends early.
4. The server sends one `RecordAnswer` per bucket, in bucket order, or an `Error` and the end.

The server stores nothing of the call, and logs only counts.

### Several nodes

A router whose scope spans several nodes sends one `RetrieveRecordsInfo` per node (`part` 0 to `n_parts` minus 1, in ascending node order). Each part describes the rows of its node only: its own `n_rows`, `n_buckets` and `spans`. The client then sends the queries of part 0, then those of part 1, and so on, each part's count being that part's shape. The answers come part by part in the same order, in bucket order inside each part, and each `RecordAnswer` carries its `part`. A single node always sends one Info with `part` 0 and `n_parts` 1, and its answers carry `part` 0.

**RetrieveRecordsRequest** (one per message)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `start` | `RetrieveRecordsStart` | one of | First message. |
| `query` | bytes | one of | One serialized ciphertext, at most `LIMIT_MAX_CHUNK_BYTES` (4 MiB). |

**RetrieveRecordsStart**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name, at most 128 bytes. |
| `key_tag` | string | yes | The collection's key tag, at most 256 bytes. |
| `pir_key_tag` | string | yes | Tag of the uploaded retrieval keys, at most 256 bytes. Public, not secret. |
| `cluster_ids` | repeated uint32 | one of | The data clusters in scope. Distinct, at most `LIMIT_MAX_CLUSTERS_PER_ROUND` (4096), each below the cluster count. |
| `all_clusters` | bool | one of | All data clusters in scope. |
| `m` | uint32 | yes | Rows wanted in the call, 1 to `LIMIT_MAX_RETRIEVE_M` (3000). The call has ceil(1.5 m) buckets. |
| `hash_seed` | bytes | yes | Exactly 16 bytes. Public. Keys the cuckoo hashing of the call. |
| `n_queries` | uint32 | no | 0 (what clients send): as many as the shape needs. A non-zero value must equal the number the shape needs, else `ERROR_INVALID_ARGUMENT`. At most `LIMIT_MAX_RETRIEVE_QUERIES` (4500). |

Give exactly one of `cluster_ids` and `all_clusters`.

**RetrieveRecordsResponse** (one per message; `api_version` plus one of)

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | 1. |
| `info` | `RetrieveRecordsInfo` | First message: the shape the server used. |
| `answer` | `RecordAnswer` | One per bucket, in bucket order. |
| `error` | `Error` | Set on failure. |

**RetrieveRecordsInfo**

| Field | Type | Description |
| --- | --- | --- |
| `n_rows` | uint64 | Database size in rows. |
| `row_size` | uint32 | Bytes per row, a multiple of 64. |
| `n_buckets` | uint32 | Answers that follow. |
| `spans` | repeated `RecordSpan` | Where the rows of each block sit in the database, in database order. |
| `part` | uint32 | Which node's database this describes. 0 on a single node. |
| `n_parts` | uint32 | Infos in the call: 1 on a node, one per node behind a router. |

**RecordSpan**: `cluster_id` (uint32), `segment` (string, opaque block name), `first_row` (uint64) and `n_rows` (uint64). Counts and names only.

**RecordAnswer**: `bucket` (uint32, 0 to `n_buckets` minus 1, in order), `answer` (bytes, one serialized compressed ciphertext) and `part` (uint32, 0 on a single node).

### What the server checks

| Check | Error |
| --- | --- |
| `api_version` is not 1 | `ERROR_UNSUPPORTED_VERSION` |
| A second `start` | `ERROR_BAD_SEQUENCE` |
| A `query` before the `start`, more queries than the call needs, or fewer when the stream ends | `ERROR_BAD_SEQUENCE` |
| A message with no part set | `ERROR_INVALID_ARGUMENT` |
| Empty `collection`, `key_tag` or `pir_key_tag`, or one that is too long | `ERROR_INVALID_ARGUMENT` |
| Both or neither of `cluster_ids` and `all_clusters`, or a repeated cluster id | `ERROR_INVALID_ARGUMENT` |
| More than 4096 cluster ids | `ERROR_SIZE_LIMIT` |
| The collection does not exist | `ERROR_COLLECTION_NOT_FOUND` |
| A cluster id at or above the collection's cluster count | `ERROR_INVALID_ARGUMENT` |
| `m` is 0, `hash_seed` is not 16 bytes, or a `query` is empty | `ERROR_INVALID_ARGUMENT` |
| `m` above 3000, `n_queries` above 4500, or a `query` above 4 MiB | `ERROR_SIZE_LIMIT` |
| `key_tag` differs from the collection's | `ERROR_KEY_TAG_MISMATCH` |
| No retrieval keys stored for the collection | `ERROR_KEYS_MISSING` |
| The stored retrieval keys carry another tag than `pir_key_tag` | `ERROR_KEY_TAG_MISMATCH` |
| The stored context is not the preset (BFV, ring dimension 8192, plaintext modulus 786433, the preset's moduli) or is below 128-bit security | `ERROR_INVALID_ARGUMENT` |
| A block in scope has no record column, or the row sizes differ | `ERROR_BLOCK_MISMATCH` |
| The scope holds no row at all | `ERROR_INVALID_ARGUMENT` |
| A non-zero `n_queries` that differs from the shape's, or a row larger than one plaintext | `ERROR_INVALID_ARGUMENT` |
| A query that is not a ciphertext of the retrieval context, or was made under another key | `ERROR_INVALID_ARGUMENT` |

A failed check ends the call with an `Error` message. An API key limited to another collection fails the call with `PERMISSION_DENIED`. The checks up to the scope happen before the Info is sent; a query is checked after it, but before the first answer.

### Record columns in scope

Every block in scope must have a record column, and all of them must have the same row size. A block without a record column, or with a different row size, fails with `ERROR_BLOCK_MISMATCH`. A stored column that is malformed fails with `ERROR_INVALID_ARGUMENT`. The row count of a column always equals the id count of its block, because the server checks that when the block is stored.

The blocks of a cluster come in the same order as in the search (`SearchScores`), so a client can map a search candidate (cluster, block number inside the cluster, slot) to its row through the spans.

An empty scope (no block, or only empty ones) is refused with `ERROR_INVALID_ARGUMENT` before the Info: there is nothing to retrieve.

### Keys

The retrieval keys are public evaluation keys of the client's retrieval key. They are not sent with each call. The client uploads them once with [`UploadKeys`](keys.md#uploadkeys), as the key kinds 4 to 6, and the call names them by (`collection`, `pir_key_tag`). The server loads them for the call under their own tag and removes them again afterwards; the search keys of the collections are not touched. Calls that load keys (including key uploads for search) are serialized on the server, so a retrieval call delays them while it computes.
