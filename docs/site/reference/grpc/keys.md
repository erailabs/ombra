# Keys

There is no kind for a secret key, and none will be added.

## KeyKind

`KEY_KIND_CONTEXT`, `KEY_KIND_EVAL_MULT` and `KEY_KIND_AUTOMORPHISM` for search. Values 4 to 6, `KEY_KIND_PIR_CONTEXT`, `KEY_KIND_PIR_EVAL_MULT` and `KEY_KIND_PIR_AUTOMORPHISM`, are the public evaluation keys of the client's retrieval key, for [`RetrieveRecords`](retrieve.md), stored per collection. `KEY_KIND_UNSPECIFIED` is 0.

## UploadKeys

```proto
rpc UploadKeys(stream UploadKeysChunk) returns (UploadKeysResponse)
```

**UploadKeysChunk** (one per message)

- `collection` (string, first chunk)
- `kind` (`KeyKind`, required): Which key file the bytes belong to.
- `offset` (uint64, required): Byte offset in this key file.
- `data` (bytes, required): At most `LIMIT_MAX_CHUNK_BYTES` (4 MiB).
- `last` (bool, required): True on the last chunk of this kind.

**UploadKeysResponse**

- `error` (`Error`)

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_INVALID_ARGUMENT` (the context is not the collection's preset's context, or a key file has a size no key of the preset has), `ERROR_KEY_TAG_MISMATCH`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_SIZE_LIMIT`, `ERROR_BAD_SEQUENCE`, `ERROR_STORAGE_FULL`.

Notes: one upload is exactly one complete key set, the three search kinds or the three retrieval kinds. A mix or a partial set is `ERROR_INVALID_ARGUMENT` and stores nothing. Retrieval files use the preset `pir_bfv_n8192` (not the collection's), sit beside the search keys and replace the previous retrieval set. Retrieval size limits per file: context 1 MiB, EvalMult key 16 MiB, automorphism keys 256 MiB (`ERROR_SIZE_LIMIT`). Insert and search fail with `ERROR_KEYS_MISSING` until keys are uploaded.
