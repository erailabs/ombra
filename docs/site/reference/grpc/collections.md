# Collections

Messages are on the [Messages](messages.md) page. The same four operations exist on the [REST API](../rest/index.md).

## CreateCollection

```proto
rpc CreateCollection(CreateCollectionRequest) returns (CreateCollectionResponse)
```

**CreateCollectionRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `spec` | `CollectionSpec` | yes | The collection to create. |

**CreateCollectionResponse** (one of)

| Field | Type | Description |
| --- | --- | --- |
| `spec` | `CollectionSpec` | The stored spec. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_EXISTS`, `ERROR_ALIAS_EXISTS` (the name is an alias), `ERROR_INVALID_ARGUMENT`, `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_FILTER_LAYOUT`, `ERROR_SIZE_LIMIT`.

Notes: the new collection takes no inserts or searches until keys are uploaded with [`UploadKeys`](keys.md#uploadkeys).

## GetCollection

Returns the `CollectionInfo` of one collection.

```proto
rpc GetCollection(GetCollectionRequest) returns (GetCollectionResponse)
```

**GetCollectionRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Collection name or [alias](aliases.md). |

**GetCollectionResponse** (one of)

| Field | Type | Description |
| --- | --- | --- |
| `info` | `CollectionInfo` | Spec, counts and `keys_uploaded`. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_INVALID_ARGUMENT`.

Notes: the only method that resolves an alias; the real name is in `spec.name`.

## ListCollections

Returns the `CollectionInfo` of every collection.

```proto
rpc ListCollections(ListCollectionsRequest) returns (ListCollectionsResponse)
```

**ListCollectionsRequest**: no fields.

**ListCollectionsResponse**

| Field | Type | Description |
| --- | --- | --- |
| `collections` | repeated `CollectionInfo` | One entry per collection. |

Errors: none in the message.

Notes: a key limited to one collection sees only that collection.

## DropCollection

```proto
rpc DropCollection(DropCollectionRequest) returns (DropCollectionResponse)
```

**DropCollectionRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Collection name. |

**DropCollectionResponse**

| Field | Type | Description |
| --- | --- | --- |
| `error` | `Error` | Unset on success. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_ALIAS_CONFLICT` (an alias points at the collection).

Notes: a collection that is only an alias's `previous` target can be dropped. See [Aliases](aliases.md).
