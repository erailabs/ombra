# Blocks

The server cannot drop single deleted slots. It drops blocks whose vectors are all deleted. For partly deleted blocks the client repacks: `GetBlock`, decrypt, drop deleted slots, re-encrypt, `ReplaceBlock`. The server never sees keys or plaintext in this flow.

## BlockInfo

| Field | Type | Description |
| --- | --- | --- |
| `segment` | string | Opaque name, stable until the block is replaced or dropped. |
| `cluster_id` | uint32 | Cluster of the block. Equal to `cluster_count` for center blocks. |
| `n_vectors` | uint64 | Occupied slots, deleted ones included. |
| `n_deleted` | uint64 | Deleted slots. |

## ListBlocks

```proto
rpc ListBlocks(ListBlocksRequest) returns (ListBlocksResponse)
```

**ListBlocksRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |

**ListBlocksResponse**

| Field | Type | Description |
| --- | --- | --- |
| `blocks` | repeated `BlockInfo` | One entry per block. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.

## GetBlock

```proto
rpc GetBlock(GetBlockRequest) returns (stream GetBlockResponse)
```

**GetBlockRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |
| `segment` | string | yes | Block name from `ListBlocks`. |

**GetBlockResponse** (one per message; one of)

| Field | Type | Description |
| --- | --- | --- |
| `header` | `BlockHeader` | First message. |
| `column` | bytes | One envelope, in column order. `n_columns` of them follow the header. |
| `error` | `Error` | Set on failure. |

**BlockHeader**

| Field | Type | Description |
| --- | --- | --- |
| `cluster_id` | uint32 | Cluster of the block. |
| `ids` | repeated bytes | Ids in slot order. |
| `deleted` | repeated bool | Parallel to `ids`: tombstoned at this instant. |
| `n_columns` | uint32 | The number of column messages that follow. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_BLOCK_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.

## ReplaceBlock

```proto
rpc ReplaceBlock(stream ReplaceBlockRequest) returns (ReplaceBlockResponse)
```

The client sends one header, then the columns in order, one per message.

**ReplaceBlockRequest** (one per message; one of)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `header` | `ReplaceBlockHeader` | first message | Names the old blocks and the new ids. |
| `column` | bytes | after the header | One envelope, in column order. |
| `record_column` | bytes | optional, after the header | A part of the new block's record column. See below. |

**ReplaceBlockHeader**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |
| `segments` | repeated string | yes | One or more blocks of one data cluster, from `ListBlocks` or `GetBlock`. |
| `ids` | repeated bytes | yes | Exactly the union of their live ids, in the new slot order, at most one block of slots. |

**ReplaceBlockResponse**

| Field | Type | Description |
| --- | --- | --- |
| `segment` | string | Name of the new block. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_BLOCK_NOT_FOUND`, `ERROR_BLOCK_MISMATCH` (the ids are not exactly the live ids, or the cluster or columns differ), `ERROR_BAD_SEQUENCE`, `ERROR_KEY_TAG_MISMATCH`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_SIZE_LIMIT`, `ERROR_STORAGE_FULL`.

Record column: the new block may carry one, sent as `record_column` parts after the columns, with the rules of [`Insert`](insert-and-delete.md#record-column), one row per header id in new slot order. Old record files are removed with the old blocks and not copied: a client that uses record columns must send the new column with every replacement. A malformed column or wrong row count fails with `ERROR_INVALID_ARGUMENT`.
