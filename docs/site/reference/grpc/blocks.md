# Blocks

A deleted vector is one slot in every column of its block, so the server cannot drop single slots. It drops blocks whose vectors are all deleted. For blocks with some deleted slots the client repacks: `GetBlock`, decrypt, drop the deleted slots, re-encrypt, `ReplaceBlock`. The server never sees keys or plaintext in this flow. Responses carry counts only, no ids in `ListBlocks`.

## BlockInfo

| Field | Type | Description |
| --- | --- | --- |
| `segment` | string | Opaque name, stable until the block is replaced or dropped. |
| `cluster_id` | uint32 | Cluster of the block. Equal to `cluster_count` for center blocks. |
| `n_vectors` | uint64 | Occupied slots, deleted ones included. |
| `n_deleted` | uint64 | Deleted slots. |

## ListBlocks

Lists the blocks (storage segments) of a collection with their vector and deleted counts. No ids.

```proto
rpc ListBlocks(ListBlocksRequest) returns (ListBlocksResponse)
```

Scope: `read`.

**ListBlocksRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `collection` | string | yes | Collection name. |

**ListBlocksResponse**

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | 1. |
| `blocks` | repeated `BlockInfo` | One entry per block. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.

## GetBlock

Streams one block: a header, then its column ciphertexts in order. The client uses it to repack.

```proto
rpc GetBlock(GetBlockRequest) returns (stream GetBlockResponse)
```

Scope: `read`.

**GetBlockRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `collection` | string | yes | Collection name. |
| `segment` | string | yes | Block name from `ListBlocks`. |

**GetBlockResponse** (one per message; `api_version` plus one of)

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | 1. |
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

Replaces one or more blocks of one cluster with one new block.

```proto
rpc ReplaceBlock(stream ReplaceBlockRequest) returns (ReplaceBlockResponse)
```

Scope: `write`.

The client sends one header, then the columns in column order, one per message (the same layout as the old blocks).

**ReplaceBlockRequest** (one per message; `api_version` plus one of)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `header` | `ReplaceBlockHeader` | first message | Names the old blocks and the new ids. |
| `column` | bytes | after the header | One envelope, in column order. |

**ReplaceBlockHeader**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |
| `segments` | repeated string | yes | One or more blocks of one data cluster, from `ListBlocks` or `GetBlock`. |
| `ids` | repeated bytes | yes | Exactly the union of their live ids, in the new slot order, at most one block of slots. |

**ReplaceBlockResponse**

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | 1. |
| `segment` | string | Name of the new block. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_BLOCK_NOT_FOUND`, `ERROR_BLOCK_MISMATCH` (the ids are not exactly the live ids, or the cluster or columns differ), `ERROR_BAD_SEQUENCE`, `ERROR_KEY_TAG_MISMATCH`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_SIZE_LIMIT`, `ERROR_STORAGE_FULL`.

Notes: N old blocks become one new block, so storage falls by N minus 1 blocks.
