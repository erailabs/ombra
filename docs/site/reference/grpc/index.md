# gRPC API

Package `ombra.v1`, service `Ombra`. The definition is `client/ombra/resources/ombra.proto`; the server builds from an identical copy (`proto/ombra/v1/ombra.proto`). The Python client wraps all of it.

The API never carries the secret key, plaintext vectors, queries, documents or metadata values. Ids are opaque bytes chosen by the client. The server receives ciphertexts in the [versioned envelope](../file-formats/index.md) and public evaluation keys only.

There is a small REST API for collection management and no REST data API: everything that reads or writes vectors, blobs or documents is gRPC. See the [REST API](../rest/index.md) and [Monitoring](../../operations/monitoring.md).

## Connecting and authenticating

Use TLS. The server refuses to start without it unless started with `--insecure`, and then it accepts only a loopback address. Send the API key as metadata: `authorization: Bearer <key>`. A missing or wrong key fails with `UNAUTHENTICATED`; a scope that is too low fails with `PERMISSION_DENIED`. A server started with `--no-auth` skips the check and is also limited to loopback.

A key has one scope: `read` calls the read methods, `write` also the write methods, `admin` everything. See [API keys](../../security/api-keys.md).

Messages may be up to 16 MiB each way (`--max-message-mb`). `Insert` and `ReplaceBlock` send one column per message (the optional record column in parts of at most 4 MiB); `PutBlob` and `UploadKeys` send chunks of at most 4 MiB.

## Methods

Scope in brackets.

- `CreateCollection` (write): [Collections](collections.md#createcollection)
- `GetCollection` (read): [Collections](collections.md#getcollection)
- `ListCollections` (read): [Collections](collections.md#listcollections)
- `DropCollection` (admin): [Collections](collections.md#dropcollection)
- `UploadKeys` (write, stream in): [Keys](keys.md#uploadkeys)
- `Insert` (write, stream in): [Insert and delete](insert-and-delete.md#insert)
- `Delete` (write): [Insert and delete](insert-and-delete.md#delete)
- `Search` (read, stream both ways): [Search](search.md#search)
- `ListBlocks` (read): [Blocks](blocks.md#listblocks)
- `GetBlock` (read, stream out): [Blocks](blocks.md#getblock)
- `ReplaceBlock` (write, stream in): [Blocks](blocks.md#replaceblock)
- `RetrieveRecords` (read, stream both ways): [Retrieve](retrieve.md#retrieverecords)
- `PutBlob` (write, stream in): [Blobs](blobs.md#putblob)
- `GetBlob` (read, stream out): [Blobs](blobs.md#getblob)
- `DeleteBlobs` (write): [Blobs](blobs.md#deleteblobs)
- `CreateAlias` (admin): [Aliases](aliases.md#createalias)
- `SwitchAlias` (admin): [Aliases](aliases.md#switchalias)
- `DropAlias` (admin): [Aliases](aliases.md#dropalias)
- `ListAliases` (read): [Aliases](aliases.md#listaliases)

Shared messages are on the [Messages](messages.md) page.

## Limits

- `LIMIT_API_VERSION`: 1
- `LIMIT_MAX_CHUNK_BYTES`: 4194304 (one `bytes` payload of a chunk)
- `LIMIT_MAX_NAME_BYTES`: 128 (collection name)
- `LIMIT_MAX_ID_BYTES`: 256 (one vector id)
- `LIMIT_MAX_IDS_PER_REQUEST`: 100000 (delete ids)
- `LIMIT_MAX_FILTER_FIELDS`: 16 (a preset's headroom may lower it, see [Presets](../presets.md))
- `LIMIT_MAX_FILTER_CARDINALITY`: 64 values per field
- `LIMIT_MAX_CLUSTERS_PER_ROUND`: 4096
- `LIMIT_MAX_RETRIEVE_M`: 3000 (rows wanted in one `RetrieveRecords` call)
- `LIMIT_MAX_RETRIEVE_QUERIES`: 4500 (query ciphertexts in one `RetrieveRecords` call)
- `LIMIT_BLOB_BUCKET_0` to `LIMIT_BLOB_BUCKET_3`: 16384, 262144, 2097152 and 16777216 bytes

A stored blob has exactly one bucket size, so the server learns the bucket of a document and nothing finer.

Every request and response carries `api_version`, which must be 1, else `ERROR_UNSUPPORTED_VERSION`. An incompatible API will be a new package, `ombra.v2`. The scope each method needs is in brackets in the [Methods](#methods) list.

## Errors

Responses report most failures in an `Error` field (code and message), never containing ids, ciphertext bytes or values. Failures of the call itself use gRPC statuses: `UNAUTHENTICATED`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED` (see `ERROR_STORAGE_FULL`) and `UNAVAILABLE`.

- `ERROR_UNSUPPORTED_VERSION`: `api_version` or the envelope version is not understood.
- `ERROR_COLLECTION_NOT_FOUND`: No collection with that name.
- `ERROR_COLLECTION_EXISTS`: A collection with that name exists.
- `ERROR_INVALID_ARGUMENT`: Malformed message or empty required field. Also a key upload whose context is not the preset's, or a key file of a size no key of the preset has.
- `ERROR_SIZE_LIMIT`: A limit above was exceeded, or a blob is not a bucket size.
- `ERROR_KEY_TAG_MISMATCH`: A ciphertext or key was made under another key than the collection's.
- `ERROR_PRESET_MISMATCH`: The envelope's preset differs from the collection's.
- `ERROR_INVALID_FILTER_LAYOUT`: More filter fields than the limits or the preset's headroom allow.
- `ERROR_KEYS_MISSING`: A search or insert before the evaluation keys were uploaded.
- `ERROR_BAD_SEQUENCE`: Stream messages arrived out of order.
- `ERROR_INTERNAL`: A server fault.
- `ERROR_BLOCK_NOT_FOUND`: No such block in the collection.
- `ERROR_BLOCK_MISMATCH`: `ReplaceBlock` ids are not exactly the live ids, or cluster or columns differ. `RetrieveRecords`: a block in scope has no record column, or row sizes differ.
- `ERROR_BLOB_NOT_FOUND`: No blob with this kind and id in the collection.
- `ERROR_STORAGE_FULL`: Disk or quota full; nothing stored. Sent as gRPC `RESOURCE_EXHAUSTED`; the client retries.
- `ERROR_ALIAS_NOT_FOUND`: No alias has this name.
- `ERROR_ALIAS_EXISTS`: The alias name is already a collection or an alias.
- `ERROR_ALIAS_CONFLICT`: `SwitchAlias`: the alias does not point at `expected_target`. `DropCollection`: an alias points at the collection.
- `ERROR_UNAVAILABLE`: Router only, on a `Search` stream: a node holding a needed cluster did not answer. Answers already sent are not used; the client opens a new stream and repeats the same requests with the same decoys. Other calls that cannot reach a node fail with gRPC `UNAVAILABLE`.
