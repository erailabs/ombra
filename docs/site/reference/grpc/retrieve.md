# Retrieve

Private fetch of the re-rank rows of a client's candidates, without the server learning which rows. The rows are the [record columns](insert-and-delete.md#record-column) stored with the blocks. `Error` is on the [Messages](messages.md) page.

The server answers on OpenFHE (BFV, preset `pir_bfv_n8192`). The CUDA build runs the products on the GPU with the same bytes as OpenFHE; without a usable GPU everything runs on the CPU.

## RetrieveRecords

```proto
rpc RetrieveRecords(stream RetrieveRecordsRequest) returns (stream RetrieveRecordsResponse)
```

The database of one call is the concatenation of the record columns of the blocks in scope: the listed data clusters, or all of them. Blocks of a cluster are in creation order, clusters in ascending id order. Center blocks hold no records and are never in scope.

The order of the stream matters, because the number of queries depends on the rows in scope:

1. The client sends one `RetrieveRecordsStart`.
2. The server checks the start, keys and scope, and answers with one `RetrieveRecordsInfo`, or an `Error` and the end. The row index of a candidate is `span.first_row` plus its slot in the block.
3. The client sends exactly as many `query` messages as the shape needs (`shape.n_cts`, computed from the Info as the server does), then half-closes. The server computes only after that. An extra query, or a stream that ends early, is `ERROR_BAD_SEQUENCE`.
4. The server sends one `RecordAnswer` per bucket, in bucket order, or an `Error` and the end.

The server stores nothing of the call and logs only counts.

### Several nodes

A router whose scope spans several nodes sends one Info per node (`part` 0 to `n_parts` minus 1, ascending node order), each with its own `n_rows`, `n_buckets` and `spans`. The client sends the queries of part 0, then part 1, and so on. Answers come in the same order, each `RecordAnswer` carrying its `part`. A single node sends one Info with `part` 0 and `n_parts` 1.

**RetrieveRecordsRequest** (one per message)

- `start` (`RetrieveRecordsStart`, one of): First message.
- `query` (bytes, one of): One serialized ciphertext, at most `LIMIT_MAX_CHUNK_BYTES` (4 MiB).

**RetrieveRecordsStart**

- `collection` (string, required): Collection name, at most 128 bytes.
- `key_tag` (string, required): The collection's key tag, at most 256 bytes.
- `pir_key_tag` (string, required): Tag of the uploaded retrieval keys, at most 256 bytes. Public, not secret.
- `cluster_ids` (repeated uint32, one of): The data clusters in scope. Distinct, at most `LIMIT_MAX_CLUSTERS_PER_ROUND` (4096), each below the cluster count.
- `all_clusters` (bool, one of): All data clusters in scope.
- `m` (uint32, required): Rows wanted in the call, 1 to `LIMIT_MAX_RETRIEVE_M` (3000). The call has ceil(1.5 m) buckets.
- `hash_seed` (bytes, required): Exactly 16 bytes. Public. Keys the cuckoo hashing of the call.
- `n_queries` (uint32, optional): 0 (what clients send): as many as the shape needs. A non-zero value must equal the number the shape needs, else `ERROR_INVALID_ARGUMENT`. At most `LIMIT_MAX_RETRIEVE_QUERIES` (4500).

Give exactly one of `cluster_ids` and `all_clusters`.

**RetrieveRecordsResponse** (one per message; one of)

- `info` (`RetrieveRecordsInfo`): First message: the shape the server used.
- `answer` (`RecordAnswer`): One per bucket, in bucket order.
- `error` (`Error`)

**RetrieveRecordsInfo**

- `n_rows` (uint64): Database size in rows.
- `row_size` (uint32): Bytes per row, a multiple of 64.
- `n_buckets` (uint32): Answers that follow.
- `spans` (repeated `RecordSpan`): Where the rows of each block sit in the database, in database order.
- `part` (uint32): Which node's database this describes. 0 on a single node.
- `n_parts` (uint32): Infos in the call: 1 on a node, one per node behind a router.

**RecordSpan**: `cluster_id` (uint32), `segment` (string, opaque block name), `first_row` (uint64) and `n_rows` (uint64). Counts and names only.

**RecordAnswer**: `bucket` (uint32, 0 to `n_buckets` minus 1, in order), `answer` (bytes, one serialized compressed ciphertext) and `part` (uint32, 0 on a single node).

### What the server checks

A failed check ends the call with an `Error`. An API key limited to another collection fails with `PERMISSION_DENIED`. Checks up to the scope happen before the Info; a query is checked after it, before the first answer.

- `ERROR_UNSUPPORTED_VERSION`: `api_version` is not 1.
- `ERROR_BAD_SEQUENCE`: a second `start`; a `query` before the `start`; more queries than the call needs, or fewer when the stream ends.
- `ERROR_INVALID_ARGUMENT`: a message with no part set; empty or too long `collection`, `key_tag` or `pir_key_tag`; both or neither of `cluster_ids` and `all_clusters`; a repeated cluster id, or one at or above the cluster count; `m` of 0; `hash_seed` not 16 bytes; an empty `query`; an empty scope; a non-zero `n_queries` that differs from the shape's; a row larger than one plaintext; a query that is not a ciphertext of the retrieval context or was made under another key; a stored context that is not the preset (BFV, ring dimension 8192, plaintext modulus 786433, the preset's moduli) or is below 128-bit security.
- `ERROR_SIZE_LIMIT`: more than 4096 cluster ids; `m` above 3000; `n_queries` above 4500; a `query` above 4 MiB.
- `ERROR_COLLECTION_NOT_FOUND`: no such collection.
- `ERROR_KEY_TAG_MISMATCH`: `key_tag` differs from the collection's, or the stored retrieval keys carry another tag than `pir_key_tag`.
- `ERROR_KEYS_MISSING`: no retrieval keys stored for the collection.
- `ERROR_BLOCK_MISMATCH`: a block in scope has no record column, or row sizes differ.
### Record columns in scope

Every block in scope needs a record column, all with the same row size; otherwise `ERROR_BLOCK_MISMATCH`. A malformed stored column fails with `ERROR_INVALID_ARGUMENT`. Blocks of a cluster are in the same order as in search (`SearchScores`), so a client can map a candidate (cluster, block number, slot) to its row through the spans. An empty scope is refused with `ERROR_INVALID_ARGUMENT` before the Info.

### Keys

The retrieval keys are public evaluation keys. The client uploads them once with [`UploadKeys`](keys.md#uploadkeys) as key kinds 4 to 6, and each call names them by (`collection`, `pir_key_tag`). The server loads them for the call under their own tag and removes them afterwards; the search keys are not touched. Calls that load keys (including search key uploads) are serialized, so a retrieval call delays them while it computes.
