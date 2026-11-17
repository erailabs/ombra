# Create a collection

`POST /v1/collections`

Creates an empty collection. Scope: `write`. Same as gRPC `CreateCollection`.

This call makes an empty collection with metadata only. It has no keys, so it cannot take inserts or searches yet. The client makes the HE keys and the `key_tag`, and sends the context and the evaluation keys with `UploadKeys` over gRPC. For that reason the Python client's `create_collection` is the usual way to start; use this route to prepare or check collections from a script.

A key limited to one collection can create only a collection with its own name.

## Parameters

JSON body of at most 64 KiB. An unknown field is refused.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Collection name. |
| `preset` | string | yes | HE preset. |
| `dimension` | number | yes | Vector dimension. |
| `security_level` | string | yes | `fast`, `decoy` or `strict`. |
| `key_tag` | string | yes | Public tag of the client's key. |
| `cluster_count` | number | yes | Number of clusters. |
| `filter_fields` | array | no | Objects with `field_id` and `cardinality`. |
| `api_version` | number | no | Default 1. |

The server checks the same rules as `CreateCollection` over gRPC: the preset, the filter layout, `--max-cluster-count` and `--max-collections`.

## Response

The collection as in [Get a collection](get-collection.md#response).

```json
{
  "name": "docs",
  "preset": "m0_depth2",
  "dimension": 384,
  "security_level": "fast",
  "key_tag": "a1b2",
  "cluster_count": 64,
  "filter_fields": [{"field_id": 0, "cardinality": 3}],
  "vector_count": 0,
  "deleted_count": 0,
  "live_count": 0,
  "keys_uploaded": false,
  "disk_bytes": 0,
  "blob_count": 0,
  "blob_bytes": 0
}
```

## Status codes

| Status | When |
| --- | --- |
| 201 | Created. |
| 400 | Bad JSON, a missing or unknown field, or a spec the server refuses (`INVALID_ARGUMENT`, `INVALID_FILTER_LAYOUT`, `UNSUPPORTED_VERSION`, `SIZE_LIMIT`). |
| 401 | No key, or an unknown key. |
| 403 | The scope is too low, or the key is limited to another name. |
| 409 | The name is taken (`COLLECTION_EXISTS`). |
| 413 | The body is over 64 KiB. |
| 503 | The server is shutting down, or its disk is full. |

See [Errors](index.md#errors) for the full table.

## Example

```bash
curl -s -X POST http://127.0.0.1:8080/v1/collections \
  -H "Authorization: Bearer $OMBRA_KEY" \
  -d '{"name": "docs", "preset": "m0_depth2", "dimension": 384,
       "security_level": "fast", "key_tag": "a1b2", "cluster_count": 64,
       "filter_fields": [{"field_id": 0, "cardinality": 3}]}'
```
