# gRPC API

The server speaks gRPC, package `ombra.v1`, service `Ombra`. The definition is `client/ombra/resources/ombra.proto`, and the server builds from the same file (`proto/ombra/v1/ombra.proto` is an identical copy). The Python client wraps all of this; you need it only to write your own client or to debug one.

What the API never carries: the secret key, plaintext vectors, queries, documents or metadata values. Ids are opaque bytes chosen by the client. The server receives ciphertexts inside the [versioned envelope](../file-formats/index.md) and public evaluation keys only.

## Is there a REST API?

There is a small REST API for collection management and no REST data API. Everything that reads or writes vectors, blobs or documents goes over gRPC. The server has an HTTP port, turned on with `--rest-port` (the Docker image turns it on at 8080). It has `/healthz` and `/readyz` (no key), `/metrics` (admin key) and `/v1/collections` to list, create, inspect and drop collections. See the [REST API](../rest/index.md) and [Monitoring](../../operations/monitoring.md).

## Connecting and authenticating

Use TLS. The server refuses to start without it unless it is started with `--insecure`, and then it only accepts a loopback address. Send the API key as call metadata: `authorization: Bearer <key>`. A missing or wrong key fails with `UNAUTHENTICATED`. A key whose scope is too low fails with `PERMISSION_DENIED`. A server started with `--no-auth` skips the check and is also limited to loopback.

An API key has one scope. `read` may call the read methods, `write` may also call the write methods, and `admin` may call everything. The table below gives the scope each method needs. See [API keys](../../security/api-keys.md).

Messages may be up to 16 MiB in both directions (`--max-message-mb`). Calls that move big data send it in parts: `Insert` and `ReplaceBlock` send one column per message, `PutBlob` and `UploadKeys` send chunks of at most 4 MiB.

## Methods

A method marked "stream" sends or receives a sequence of messages.

| Method | Scope | What it does | Page |
| --- | --- | --- | --- |
| `CreateCollection` | write | Creates a collection from a `CollectionSpec` and returns the stored spec. | [Collections](collections.md#createcollection) |
| `GetCollection` | read | Returns the `CollectionInfo`: spec, vector counts, and whether evaluation keys are uploaded. | [Collections](collections.md#getcollection) |
| `ListCollections` | read | Returns the `CollectionInfo` of every collection. | [Collections](collections.md#listcollections) |
| `DropCollection` | admin | Deletes a collection and everything stored for it. | [Collections](collections.md#dropcollection) |
| `UploadKeys` (stream in) | write | Uploads the public evaluation keys in chunks. | [Keys](keys.md#uploadkeys) |
| `Insert` (stream in) | write | Inserts blocks of vectors, one cluster per block. | [Insert and delete](insert-and-delete.md#insert) |
| `Delete` | write | Marks the given ids deleted. | [Insert and delete](insert-and-delete.md#delete) |
| `Search` (stream both ways) | read | One bidirectional stream per query, in two rounds. | [Search](search.md#search) |
| `ListBlocks` | read | Lists the blocks of a collection with their vector and deleted counts. | [Blocks](blocks.md#listblocks) |
| `GetBlock` (stream out) | read | Streams one block: a header, then its column ciphertexts. | [Blocks](blocks.md#getblock) |
| `ReplaceBlock` (stream in) | write | Replaces one or more blocks of one cluster with one new block. | [Blocks](blocks.md#replaceblock) |
| `PutBlob` (stream in) | write | Stores an encrypted blob under (collection, kind, id). | [Blobs](blobs.md#putblob) |
| `GetBlob` (stream out) | read | Streams a blob: its size, then data chunks. | [Blobs](blobs.md#getblob) |
| `DeleteBlobs` | write | Deletes blobs by id. | [Blobs](blobs.md#deleteblobs) |
| `CreateAlias` | admin | Makes a name an alias of one collection. | [Aliases](aliases.md#createalias) |
| `SwitchAlias` | admin | Moves an alias to another collection if it points at the expected one now. | [Aliases](aliases.md#switchalias) |
| `DropAlias` | admin | Removes an alias. The collection stays. | [Aliases](aliases.md#dropalias) |
| `ListAliases` | read | Lists the aliases. | [Aliases](aliases.md#listaliases) |

Shared messages (`Error`, `CollectionSpec`, `CollectionInfo`, `FilterField` and the enums) are on the [Messages](messages.md) page.

## Limits

These are the values of the `Limit` enum.

| Limit | Value |
| --- | --- |
| `LIMIT_API_VERSION` | 1 |
| `LIMIT_MAX_CHUNK_BYTES` | 4194304 (one `bytes` payload of a chunk) |
| `LIMIT_MAX_NAME_BYTES` | 128 (collection name) |
| `LIMIT_MAX_ID_BYTES` | 256 (one vector id) |
| `LIMIT_MAX_IDS_PER_REQUEST` | 100000 (delete ids) |
| `LIMIT_MAX_FILTER_FIELDS` | 16 (a preset's headroom may lower it, see [Presets](../presets.md)) |
| `LIMIT_MAX_FILTER_CARDINALITY` | 64 values per field |
| `LIMIT_MAX_CLUSTERS_PER_ROUND` | 4096 |
| `LIMIT_BLOB_BUCKET_0` to `LIMIT_BLOB_BUCKET_3` | 16384, 262144, 2097152 and 16777216 bytes |

A stored blob has exactly one bucket size, so the server learns the bucket of a document and nothing finer.

Every message that the server stores carries `api_version`, which must be 1. Any other value fails with `ERROR_UNSUPPORTED_VERSION`. An incompatible API will be a new package, `ombra.v2`, never a changed field.

## Errors

Responses report most failures in an `Error` field (code and message) instead of a gRPC status. The message never contains ids, ciphertext bytes or values. Failures of the call itself use gRPC statuses: `UNAUTHENTICATED`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED` (see `ERROR_STORAGE_FULL`) and `UNAVAILABLE`.

| Code | Meaning |
| --- | --- |
| `ERROR_UNSUPPORTED_VERSION` | `api_version` or the envelope version is not understood. |
| `ERROR_COLLECTION_NOT_FOUND` | No collection with that name. |
| `ERROR_COLLECTION_EXISTS` | A collection with that name exists. |
| `ERROR_INVALID_ARGUMENT` | Malformed message or an empty required field. A key upload is refused with this code when the context is not the collection's preset's context, or a key file has a size no key of the preset has. |
| `ERROR_SIZE_LIMIT` | A limit above was exceeded, or a blob is not a bucket size. |
| `ERROR_KEY_TAG_MISMATCH` | A ciphertext or key was made under another key than the collection's. |
| `ERROR_PRESET_MISMATCH` | The envelope's preset differs from the collection's. |
| `ERROR_INVALID_FILTER_LAYOUT` | More filter fields than the limits or the preset's headroom allow. |
| `ERROR_KEYS_MISSING` | A search or insert before the evaluation keys were uploaded. |
| `ERROR_BAD_SEQUENCE` | Stream messages arrived out of order. |
| `ERROR_INTERNAL` | A server fault. |
| `ERROR_BLOCK_NOT_FOUND` | No such block in the collection. |
| `ERROR_BLOCK_MISMATCH` | The ids of a `ReplaceBlock` are not exactly the live ids, or the cluster or columns differ. |
| `ERROR_BLOB_NOT_FOUND` | No blob with this kind and id in the collection. |
| `ERROR_STORAGE_FULL` | The server's disk or quota is full. Nothing was stored. The server sends this as the gRPC status `RESOURCE_EXHAUSTED`, and the client retries. |
| `ERROR_ALIAS_NOT_FOUND` | No alias has this name. |
| `ERROR_ALIAS_EXISTS` | The alias name is already a collection or an alias. |
| `ERROR_ALIAS_CONFLICT` | `SwitchAlias`: the alias does not point at `expected_target`. `DropCollection`: an alias points at the collection. |
| `ERROR_UNAVAILABLE` | Sent only by a router, and only on a `Search` stream: a node that holds a needed cluster did not answer. The answers of the round that were already sent are not used. The client opens a new stream and sends the same requests again, with the same decoys. Other calls that cannot reach a node fail with the gRPC status `UNAVAILABLE`. |
