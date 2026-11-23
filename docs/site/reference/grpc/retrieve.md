# Retrieve

Private fetch of the re-rank rows of a client's candidates, so that the server does not learn which rows. The rows are the [record columns](insert-and-delete.md#record-column) stored with the blocks. `Error` is on the [Messages](messages.md) page.

!!! note "Not available yet"
    The server reads and validates the request stream, and then ends the call with the gRPC status `UNIMPLEMENTED`. It sends no `RetrieveRecordsInfo` and no answer. The messages below are the contract that the answer will use.

## RetrieveRecords

```proto
rpc RetrieveRecords(stream RetrieveRecordsRequest) returns (stream RetrieveRecordsResponse)
```

Scope: `read`.

The database of one call is the concatenation of the record columns of the blocks in scope. The scope is the listed data clusters, or all data clusters. The blocks of a cluster come in the order they were created, and the clusters in ascending id order. Row i of the database is the i-th row of that concatenation. Center blocks hold no records and are never in scope.

The client sends one `RetrieveRecordsStart`, then exactly `n_queries` messages with a `query`. The server answers with one `RetrieveRecordsInfo`, then one `RecordAnswer` per bucket, or an `Error` and the end. The server stores nothing of the call.

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
| `n_queries` | uint32 | yes | Query messages that follow, 1 to `LIMIT_MAX_RETRIEVE_QUERIES` (4500). |

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

**RecordSpan**: `cluster_id` (uint32), `segment` (string, opaque block name), `first_row` (uint64) and `n_rows` (uint64). Counts and names only.

**RecordAnswer**: `bucket` (uint32, 0 to `n_buckets` minus 1, in order) and `answer` (bytes, one serialized compressed ciphertext).

### What the server checks today

| Check | Error |
| --- | --- |
| `api_version` is not 1 | `ERROR_UNSUPPORTED_VERSION` |
| A second `start` | `ERROR_BAD_SEQUENCE` |
| A `query` before the `start`, more queries than `n_queries`, or fewer when the stream ends | `ERROR_BAD_SEQUENCE` |
| A message with no part set | `ERROR_INVALID_ARGUMENT` |
| Empty `collection`, `key_tag` or `pir_key_tag`, or one that is too long | `ERROR_INVALID_ARGUMENT` |
| Both or neither of `cluster_ids` and `all_clusters`, or a repeated cluster id | `ERROR_INVALID_ARGUMENT` |
| More than 4096 cluster ids | `ERROR_SIZE_LIMIT` |
| `m` is 0, `hash_seed` is not 16 bytes, `n_queries` is 0, or a `query` is empty | `ERROR_INVALID_ARGUMENT` |
| `m` above 3000, `n_queries` above 4500, or a `query` above 4 MiB | `ERROR_SIZE_LIMIT` |

A failed check ends the call with an `Error` message. An API key limited to another collection fails the call with `PERMISSION_DENIED`. The server does not look up the collection, the cluster range or the stored keys yet.

### Record columns in scope

When the answer is implemented, every block in scope must have a record column, and all of them must have the same row size. A block without a record column, or with a different row size, fails with `ERROR_BLOCK_MISMATCH`. A stored column that is malformed fails with `ERROR_INVALID_ARGUMENT`. The row count of a column always equals the id count of its block, because the server checks that when the block is stored.

### Keys

The retrieval keys are public evaluation keys of the client's retrieval key. They are not sent with each call. The client uploads them once with [`UploadKeys`](keys.md#uploadkeys), as the key kinds 4 to 6, and the call names them by (`collection`, `pir_key_tag`). The server does not store them yet. Once it does, `ERROR_KEYS_MISSING` will mean that none are stored, and `ERROR_KEY_TAG_MISMATCH` that the stored ones carry another tag.
