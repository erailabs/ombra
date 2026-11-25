# List collections

`GET /v1/collections`

Scope: `read`. Same as gRPC `ListCollections`. A key limited to one collection sees only that collection.

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

Statuses: 200; 401; 403 scope too low; 503. See [Errors](index.md#errors).
