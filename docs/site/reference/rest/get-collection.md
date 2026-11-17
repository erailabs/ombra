# Get a collection

`GET /v1/collections/{name}`

Returns the statistics of one collection. Scope: `read`. Same as gRPC `GetCollection`.

If `{name}` is an [alias](../../operations/migration.md#aliases), it answers for the collection the alias points at and `name` in the answer is the real name. There are no REST routes to create, switch or drop aliases yet; use gRPC or the Python client.

A key limited to one collection gets `403` for any other name, whether the collection exists or not.

## Parameters

| Name | In | Required | Description |
| --- | --- | --- | --- |
| `name` | path | yes | Collection name or alias, percent-decoded. At most 128 bytes, no `/` or control character. |

## Response

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Collection name. |
| `preset` | string | HE preset. |
| `dimension` | number | Vector dimension. |
| `security_level` | string | `fast`, `decoy` or `strict`. |
| `key_tag` | string | Public tag of the client's key. It is not a key. |
| `cluster_count` | number | Number of clusters. |
| `filter_fields` | array | Objects with `field_id` and `cardinality`. |
| `vector_count` | number | Vectors stored. Includes deleted vectors until [compaction](../../how-it-works/deletes.md) drops their blocks. |
| `deleted_count` | number | Deleted vectors. |
| `live_count` | number | Vectors not deleted. |
| `keys_uploaded` | boolean | Whether the evaluation keys are uploaded. |
| `disk_bytes` | number | Size of the collection's segment files, key files and blobs. It leaves out the catalog and the write-ahead log. |
| `blob_count` | number | Blobs stored. |
| `blob_bytes` | number | Bytes of blobs stored. |

Nothing in the answer comes from the data itself.

```json
{
  "name": "docs",
  "preset": "m0_depth2",
  "dimension": 384,
  "security_level": "fast",
  "key_tag": "a1b2",
  "cluster_count": 64,
  "filter_fields": [{"field_id": 0, "cardinality": 3}],
  "vector_count": 1000,
  "deleted_count": 12,
  "live_count": 988,
  "keys_uploaded": true,
  "disk_bytes": 123456789,
  "blob_count": 0,
  "blob_bytes": 0
}
```

## Status codes

| Status | When |
| --- | --- |
| 200 | Success. |
| 401 | No key, or an unknown key. |
| 403 | The scope is too low, or the key is limited to another collection. |
| 404 | No such collection (`COLLECTION_NOT_FOUND`), or an unknown route. |
| 503 | The server is shutting down, or its disk is full. |

See [Errors](index.md#errors) for the full table.

## Example

```bash
curl -s http://127.0.0.1:8080/v1/collections/docs \
  -H "Authorization: Bearer $OMBRA_KEY"
```
