# Blobs

Blobs are opaque client ciphertexts, keyed by (collection, kind, id). The server never parses them. The size must be one of the four bucket sizes (see [Limits](index.md#limits)), else `ERROR_SIZE_LIMIT`. A blob becomes visible only after it is written, synced and committed, so a crash leaves the old or the new blob, never a part.

## BlobKind

- `BLOB_KIND_RECORD`: Per-vector record: encrypted vector copy and metadata.
- `BLOB_KIND_DOCUMENT`: Document body, or an encrypted reference to the customer's storage.

`BLOB_KIND_UNSPECIFIED` is 0.

## PutBlob

```proto
rpc PutBlob(stream PutBlobRequest) returns (PutBlobResponse)
```

The client sends one header, then `data` chunks of at most 4 MiB summing to `size`. An existing blob with the same key is replaced atomically.

**PutBlobRequest** (one per message; one of)

- `header` (`PutBlobHeader`, first message): Names the blob.
- `data` (bytes, after the header): A chunk of at most `LIMIT_MAX_CHUNK_BYTES` (4 MiB).

**PutBlobHeader**

- `collection` (string, required)
- `kind` (`BlobKind`, required)
- `id` (bytes, required): At most 256 bytes.
- `size` (uint64, required): Total bytes that follow. Must be a bucket size.

**PutBlobResponse**

- `error` (`Error`)

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_SIZE_LIMIT` (not a bucket size, or an id or chunk too large), `ERROR_BAD_SEQUENCE`, `ERROR_INVALID_ARGUMENT`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_STORAGE_FULL`.

## GetBlob

```proto
rpc GetBlob(GetBlobRequest) returns (stream GetBlobResponse)
```

**GetBlobRequest**

- `collection` (string, required)
- `kind` (`BlobKind`, required)
- `id` (bytes, required)

**GetBlobResponse** (one per message; one of)

- `size` (uint64): First message: total bytes.
- `data` (bytes): Chunks in order.
- `error` (`Error`)

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_BLOB_NOT_FOUND`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.

## DeleteBlobs

```proto
rpc DeleteBlobs(DeleteBlobsRequest) returns (DeleteBlobsResponse)
```

**DeleteBlobsRequest**

- `collection` (string, required)
- `kind` (`BlobKind`, required)
- `ids` (repeated bytes, required): At most 100000 (`LIMIT_MAX_IDS_PER_REQUEST`).

**DeleteBlobsResponse**

- `deleted` (uint64): Count of blobs deleted.
- `error` (`Error`)

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_SIZE_LIMIT`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`.
