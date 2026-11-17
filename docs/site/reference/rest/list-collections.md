# List collections

`GET /v1/collections`

Returns one entry per collection. Scope: `read`. Same as gRPC `ListCollections`.

A key limited to one collection sees only that collection in the list.

## Parameters

None.

## Response

`{"collections": [...]}`. Each entry has the fields of [Get a collection](get-collection.md#response), without the size fields (`disk_bytes`, `blob_count`, `blob_bytes`).

```json
{
  "collections": [
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
      "keys_uploaded": true
    }
  ]
}
```

## Status codes

| Status | When |
| --- | --- |
| 200 | Success. |
| 401 | No key, or an unknown key. |
| 403 | The scope is too low. |
| 503 | The server is shutting down, or its disk is full. |

See [Errors](index.md#errors) for the full table.

## Example

```bash
curl -s http://127.0.0.1:8080/v1/collections \
  -H "Authorization: Bearer $OMBRA_KEY"
```
