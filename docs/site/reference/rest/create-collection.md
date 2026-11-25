# Create a collection

`POST /v1/collections`

Scope: `write`. Same as gRPC `CreateCollection`.

It makes an empty collection with metadata only. Keys are uploaded with `UploadKeys` over gRPC, so the Python client's `create_collection` is the usual way to start. A key limited to one collection can create only a collection with its own name.

## Parameters

JSON body of at most 64 KiB. An unknown field is refused.

- `name` (string, required)
- `preset` (string, required)
- `dimension` (number, required)
- `security_level` (string, required): `fast`, `decoy` or `strict`.
- `key_tag` (string, required): Public tag of the client's key.
- `cluster_count` (number, required)
- `filter_fields` (array, optional): Objects with `field_id` and `cardinality`.
- `api_version` (number, optional): Default 1.

The rules are those of gRPC `CreateCollection`: preset, filter layout, `--max-cluster-count`, `--max-collections`.

## Response

The collection as in [Get a collection](get-collection.md#response).

## Status codes

Statuses: 201 created; 400 bad JSON, a missing or unknown field, or a refused spec (`INVALID_ARGUMENT`, `INVALID_FILTER_LAYOUT`, `UNSUPPORTED_VERSION`, `SIZE_LIMIT`); 401; 403 (scope too low, or key limited to another name); 409 name taken (`COLLECTION_EXISTS`); 413 body over 64 KiB; 503 shutting down or disk full. See [Errors](index.md#errors).
