# Insert and delete

`Error` is on the [Messages](messages.md) page.

## Insert

Inserts blocks of vectors, one cluster per block.

```proto
rpc Insert(stream InsertRequest) returns (InsertResponse)
```

Scope: `write`.

The client sends a stream of blocks in parts, so that no message is larger than one column. A block is up to one batch of vectors of one cluster: `dimension` column ciphertexts plus `sum(cardinality)` one-hot ciphertexts, each in a versioned envelope. Per block: one header, then its columns in column order, one per message. The next header, or the end of the stream, closes the block, and it is stored then.

**InsertRequest** (one per message)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `header` | `InsertHeader` | one of | Starts a block. |
| `column` | bytes | one of | One column envelope of the current block. |

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

- Idempotent: a block whose ids all exist in one stored block of the same cluster, with byte-identical column envelopes, is a success that stores nothing and counts its ids again. A client may repeat an `Insert` whose answer it never got. A block that shares only some ids with stored ones, or reuses an id with other bytes, is refused as a duplicate.
- Each block is stored when it is closed.

**InsertBlock** is one whole block (`api_version`, `collection`, `cluster_id`, `ids`, `columns` and `is_center`). No call sends it. Local mode stores blocks in this form.

## Delete

Marks the given ids deleted.

```proto
rpc Delete(DeleteRequest) returns (DeleteResponse)
```

Scope: `write`.

**DeleteRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `collection` | string | yes | Collection name. |
| `ids` | repeated bytes | yes | Ids to delete, at most 100000 (`LIMIT_MAX_IDS_PER_REQUEST`), each at most 256 bytes. |

**DeleteResponse**

| Field | Type | Description |
| --- | --- | --- |
| `deleted` | uint64 | Count of ids deleted. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`, `ERROR_SIZE_LIMIT`.

Notes: a deleted vector stays in its block as a tombstone until the block is dropped or repacked. See [Blocks](blocks.md).
