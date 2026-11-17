# Drop a collection

`DELETE /v1/collections/{name}`

Deletes the collection and everything stored for it. Scope: `admin`. Same as gRPC `DropCollection`.

It needs an admin key, which is never limited to one collection, so a limited key can never drop a collection.

## Parameters

| Name | In | Required | Description |
| --- | --- | --- | --- |
| `name` | path | yes | Collection name, percent-decoded. At most 128 bytes, no `/` or control character. |

## Response

```json
{"dropped": true}
```

## Status codes

| Status | When |
| --- | --- |
| 200 | Dropped. |
| 401 | No key, or an unknown key. |
| 403 | The scope is too low. |
| 404 | No such collection (`COLLECTION_NOT_FOUND`), or an unknown route. |
| 503 | The server is shutting down, or its disk is full. |

See [Errors](index.md#errors) for the full table.

## Example

```bash
curl -s -X DELETE http://127.0.0.1:8080/v1/collections/docs \
  -H "Authorization: Bearer $OMBRA_KEY"
```
