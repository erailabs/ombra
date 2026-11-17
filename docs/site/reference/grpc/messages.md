# Shared messages

Messages used by more than one method. Messages that belong to one method are on that method's page. Every stored message carries `api_version`, which must be 1 (see [Limits](index.md#limits)).

## Error

| Field | Type | Description |
| --- | --- | --- |
| `code` | `ErrorCode` | One of the codes in [Errors](index.md#errors). |
| `message` | string | Text for people. It never contains ids, ciphertext bytes or values. |

## FilterField

One filter field. The server sees the count of fields and values, never names. A one-hot column per value is stored for every vector.

| Field | Type | Description |
| --- | --- | --- |
| `field_id` | uint32 | 0, 1, ... in order. Opaque, not a name. |
| `cardinality` | uint32 | Number of values, 1 to 64 (`LIMIT_MAX_FILTER_CARDINALITY`). |

## CollectionSpec

Stored in the catalog.

| Field | Type | Description |
| --- | --- | --- |
| `api_version` | uint32 | Must be 1. |
| `name` | string | Collection name, at most 128 bytes. |
| `preset` | string | Name of the HE preset, see [Presets](../presets.md). |
| `dimension` | uint32 | Vector dimension. |
| `security_level` | `SecurityLevel` | See below. |
| `filter_fields` | repeated `FilterField` | The filter layout. |
| `key_tag` | string | A public tag of the client's key. It is not secret. |
| `cluster_count` | uint32 | Number of clusters. |

The query ciphertext of a collection carries `dimension + sum(cardinality)` entries.

## CollectionInfo

| Field | Type | Description |
| --- | --- | --- |
| `spec` | `CollectionSpec` | The stored spec. |
| `vector_count` | uint64 | Vectors stored, deleted ones included until their blocks are dropped. |
| `deleted_count` | uint64 | Deleted vectors. |
| `keys_uploaded` | bool | Whether the evaluation keys are uploaded. |

## SecurityLevel

`SECURITY_LEVEL_FAST`, `SECURITY_LEVEL_DECOY` and `SECURITY_LEVEL_STRICT` (and `SECURITY_LEVEL_UNSPECIFIED`, which is 0). See [Clusters](../../how-it-works/clusters.md) for what each one does.

## Enums

`Limit` is on the [index](index.md#limits) and `ErrorCode` in [Errors](index.md#errors). `KeyKind` is on the [Keys](keys.md#keykind) page, `BlobKind` on the [Blobs](blobs.md#blobkind) page.
