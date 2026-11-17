# Keys

Upload the public evaluation keys of a collection. There is no kind for a secret key, and none will be added.

## KeyKind

`KEY_KIND_CONTEXT`, `KEY_KIND_EVAL_MULT` and `KEY_KIND_AUTOMORPHISM` (and `KEY_KIND_UNSPECIFIED`, which is 0).

## UploadKeys

Uploads the public evaluation keys in chunks. `kind` says which file the bytes belong to.

```proto
rpc UploadKeys(stream UploadKeysChunk) returns (UploadKeysResponse)
```

Scope: `write`.

**UploadKeysChunk** (one per message)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_version` | uint32 | yes | Must be 1. |
| `collection` | string | first chunk | Collection name. |
| `kind` | `KeyKind` | yes | Which key file the bytes belong to. |
| `offset` | uint64 | yes | Byte offset in this key file. |
| `data` | bytes | yes | At most `LIMIT_MAX_CHUNK_BYTES` (4 MiB). |
| `last` | bool | yes | True on the last chunk of this kind. |

**UploadKeysResponse**

| Field | Type | Description |
| --- | --- | --- |
| `error` | `Error` | Unset on success. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_INVALID_ARGUMENT` (the context is not the collection's preset's context, or a key file has a size no key of the preset has), `ERROR_KEY_TAG_MISMATCH`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_SIZE_LIMIT`, `ERROR_BAD_SEQUENCE`, `ERROR_STORAGE_FULL`.

Notes: chunks are at most 4 MiB. Insert and search fail with `ERROR_KEYS_MISSING` until the keys are uploaded.
