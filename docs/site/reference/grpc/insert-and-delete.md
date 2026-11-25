# Insert and delete

## Insert

```proto
rpc Insert(stream InsertRequest) returns (InsertResponse)
```

A block is up to one batch of vectors of one cluster: `dimension` column ciphertexts plus `sum(cardinality)` one-hot ciphertexts, each in a versioned envelope. Per block the client sends one header, then the columns in order, one per message. The next header or the end of the stream closes the block, and it is stored then.

**InsertRequest** (one per message)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `header` | `InsertHeader` | one of | Starts a block. |
| `column` | bytes | one of | One column envelope of the current block. |
| `record_column` | bytes | one of, optional | A part of the current block's record column. See below. |

**InsertHeader**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |
| `cluster_id` | uint32 | yes | Cluster of the block. |
| `ids` | repeated bytes | yes | One per occupied slot, in slot order, at most one block of slots. |
| `is_center` | bool | no | A cluster-center block for stage 1 instead of data. |

**InsertResponse**

| Field | Type | Description |
| --- | --- | --- |
| `inserted` | uint64 | Count of ids in the stored blocks. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_KEYS_MISSING`, `ERROR_KEY_TAG_MISMATCH`, `ERROR_PRESET_MISMATCH`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`, `ERROR_BAD_SEQUENCE` or `ERROR_SIZE_LIMIT` (fewer or more columns than the layout needs, or a column before a header), `ERROR_STORAGE_FULL`.

Notes:

- Idempotent: a block whose ids all exist in one stored block of the same cluster, with byte-identical column envelopes, succeeds, stores nothing and counts its ids again. A block that shares only some ids, or reuses an id with other bytes, is refused as a duplicate.

### Record column

A data block may carry a record column: the sealed re-rank rows of its vectors, one per id, in the [`ORCL` layout](../file-formats/server-files.md#record-column). The server never opens it. It is for [`RetrieveRecords`](retrieve.md).

- Sent after the block's columns as `record_column` parts of at most 4 MiB, joined in order. The joined column is at most 1 GiB (`kMaxRecordColumnBytes`), else `ERROR_SIZE_LIMIT`. A part before the header is `ERROR_BAD_SEQUENCE`.
- Without parts, the block has no record column. `InsertBlock` carries the whole column in `record_column` (empty means none).
- A malformed column (magic, version, reserved field, row size, length) or a row count that differs from the id count is `ERROR_INVALID_ARGUMENT`.
- A record column on a center block (`is_center`) is refused with `ERROR_INVALID_ARGUMENT`.
- A repeated block is a replay only with byte-identical record bytes, or if both have none. Otherwise it fails as a duplicate id.

**InsertBlock** is one whole block (`api_version`, `collection`, `cluster_id`, `ids`, `columns`, `is_center`, `record_column`). No call sends it; local mode stores blocks in this form.

## Delete

```proto
rpc Delete(DeleteRequest) returns (DeleteResponse)
```

**DeleteRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |
| `ids` | repeated bytes | yes | Ids to delete, at most 100000 (`LIMIT_MAX_IDS_PER_REQUEST`), each at most 256 bytes. |

**DeleteResponse**

| Field | Type | Description |
| --- | --- | --- |
| `deleted` | uint64 | Count of ids deleted. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`, `ERROR_SIZE_LIMIT`.

Notes: a deleted vector stays as a tombstone until its block is dropped or repacked. See [Blocks](blocks.md).
