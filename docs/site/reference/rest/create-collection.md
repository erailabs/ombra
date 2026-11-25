# Create a collection

`POST /v1/collections`

Scope: `write`. Same as gRPC `CreateCollection`.

It makes an empty collection with metadata only. Keys are uploaded with `UploadKeys` over gRPC, so the Python client's `create_collection` is the usual way to start. A key limited to one collection can create only a collection with its own name.

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

The rules are those of gRPC `CreateCollection`: preset, filter layout, `--max-cluster-count`, `--max-collections`.

## Response

The collection as in [Get a collection](get-collection.md#response).

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
