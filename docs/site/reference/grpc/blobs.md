# Blobs

Blobs are opaque client ciphertexts. The server never parses them. A blob is keyed by (collection, kind, id). Its size must be one of the four bucket sizes (16384, 262144, 2097152 or 16777216 bytes, see [Limits](index.md#limits)), else `ERROR_SIZE_LIMIT`. A blob becomes visible only after it is fully written, synced and committed in the catalog, so a crash leaves the old blob or the new one, never a part.

## BlobKind

| Value | Meaning |
| --- | --- |
| `BLOB_KIND_RECORD` | Per-vector record: encrypted vector copy and metadata. |
| `BLOB_KIND_DOCUMENT` | Document body, or an encrypted reference to the customer's storage. |

`BLOB_KIND_UNSPECIFIED` is 0.

## PutBlob

Stores an encrypted blob under (collection, kind, id), replacing any blob with that key.

```proto
rpc PutBlob(stream PutBlobRequest) returns (PutBlobResponse)
```

Scope: `write`.

The client sends one header, then `data` chunks of at most 4 MiB whose lengths sum to `size`. An existing blob with the same key is replaced atomically.

**PutBlobRequest** (one per message; `api_version` plus one of)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `header` | `PutBlobHeader` | first message | Names the blob. |
| `data` | bytes | after the header | A chunk of at most `LIMIT_MAX_CHUNK_BYTES` (4 MiB). |

**PutBlobHeader**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Collection name. |
| `kind` | `BlobKind` | yes | Blob kind. |
| `id` | bytes | yes | At most 256 bytes. |
| `size` | uint64 | yes | Total bytes that follow. Must be a bucket size. |

**PutBlobResponse**

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | 1. |
| `error` | `Error` | Unset on success. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_SIZE_LIMIT` (not a bucket size, or an id or chunk too large), `ERROR_BAD_SEQUENCE`, `ERROR_INVALID_ARGUMENT`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_STORAGE_FULL`.

## GetBlob

Streams a blob: its size, then data chunks.

```proto
rpc GetBlob(GetBlobRequest) returns (stream GetBlobResponse)
```

Scope: `read`.

**GetBlobRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `collection` | string | yes | Collection name. |
| `kind` | `BlobKind` | yes | Blob kind. |
| `id` | bytes | yes | Blob id. |

**GetBlobResponse** (one per message; `api_version` plus one of)

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | 1. |
| `size` | uint64 | First message: total bytes. |
| `data` | bytes | Chunks in order. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_BLOB_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.

## DeleteBlobs

Deletes blobs by id. A missing id is not an error.

```proto
rpc DeleteBlobs(DeleteBlobsRequest) returns (DeleteBlobsResponse)
```

Scope: `write`.

**DeleteBlobsRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `collection` | string | yes | Collection name. |
| `kind` | `BlobKind` | yes | Blob kind. |
| `ids` | repeated bytes | yes | At most 100000 (`LIMIT_MAX_IDS_PER_REQUEST`). |

**DeleteBlobsResponse**

| Field | Type | Description |
| --- | --- | --- |
| `deleted` | uint64 | Count of blobs deleted. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_SIZE_LIMIT`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.
