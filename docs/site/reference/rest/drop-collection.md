# Drop a collection

`DELETE /v1/collections/{name}`

Deletes the collection and all its data. Scope: `admin` (never limited to one collection). Same as gRPC `DropCollection`.

## Parameters

- `name` (path, required): Collection name, percent-decoded. At most 128 bytes, no `/` or control character.

## Response

```json
{"dropped": true}
```

## Status codes

Statuses: 200 dropped; 401; 403 scope too low; 404 no such collection (`COLLECTION_NOT_FOUND`) or unknown route; 503. See [Errors](index.md#errors).
