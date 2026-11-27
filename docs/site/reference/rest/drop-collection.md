# Drop a collection

`DELETE /v1/collections/{name}`

Deletes the collection and all its data. Scope: `admin` (never limited to one collection). Same as gRPC `DropCollection`.

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
