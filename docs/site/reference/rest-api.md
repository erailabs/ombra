# REST API

The HTTP port has a small API to manage collections. It is for operators and scripts. It does not carry vectors, queries, documents, ids or keys: those go over [gRPC](grpc-api.md), and so do the FHE keys of a collection (`UploadKeys`) and every insert and search.

Turn the port on with `--rest-port`. The Docker image does it at 8080. The port uses the same TLS certificate as gRPC and the same API key file. By default it listens on `127.0.0.1`. See [Configuration](configuration.md) for `--rest-host`, `--rest-workers` and `--rest-max-conns`.

## Authentication

Send an API key as `Authorization: Bearer <key>`. The key, its scope and its optional collection are the ones of gRPC, and each route needs the scope of the gRPC method that does the same work:

| Route | Same as | Scope |
| --- | --- | --- |
| `GET /v1/collections` | `ListCollections` | read |
| `GET /v1/collections/{name}` | `GetCollection` | read |
| `POST /v1/collections` | `CreateCollection` | write |
| `DELETE /v1/collections/{name}` | `DropCollection` | admin |

A key limited to one collection sees only that collection in the list. For any other name it gets `403`, whether the collection exists or not. It can create only a collection with its own name. A request without a valid key gets `401`. Only `--no-auth` turns the check off, and the server accepts that only on a loopback address, as it does for gRPC. See [API keys](../security/api-keys.md).

Without TLS (`--insecure`) the port accepts only a loopback address. Behind a proxy or on another address, use TLS. A key sent over plain HTTP can be read by anyone on the path.

## Routes

Names in the path are percent-decoded. A name with a `/`, a control character or more than 128 bytes is an unknown route. Answers are JSON.

### List collections

`GET /v1/collections` returns `{"collections": [...]}` with one entry per collection (see the fields below, without the size fields).

### Get one collection

`GET /v1/collections/{name}` returns the statistics of one collection:

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

`vector_count` includes deleted vectors until [compaction](../how-it-works/deletes.md) drops their blocks. `disk_bytes` is the size of the collection's segment files, key files and blobs. It leaves out the catalog and the write-ahead log. `key_tag` is public, not a key. Nothing in the answer comes from the data itself.

### Create a collection

`POST /v1/collections` with a JSON body of at most 64 KiB:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/collections \
  -H "Authorization: Bearer $OMBRA_KEY" \
  -d '{"name": "docs", "preset": "m0_depth2", "dimension": 384,
       "security_level": "fast", "key_tag": "a1b2", "cluster_count": 64,
       "filter_fields": [{"field_id": 0, "cardinality": 3}]}'
```

All fields except `filter_fields` and `api_version` (default 1) are required. `security_level` is `fast`, `decoy` or `strict`. An unknown field is refused. The server checks the same rules as `CreateCollection` over gRPC: the preset, the filter layout, `--max-cluster-count` and `--max-collections`. The answer is `201` with the collection as above.

This call makes an empty collection with metadata only. It has no keys, so it cannot take inserts or searches yet. The client makes the FHE keys and the `key_tag`, and sends the context and the evaluation keys with `UploadKeys` over gRPC. For that reason the Python client's `create_collection` is the usual way to start; use this route to prepare or check collections from a script.

### Drop a collection

`DELETE /v1/collections/{name}` deletes the collection and everything stored for it, and returns `{"dropped": true}`. It needs an admin key, which is never limited to one collection.

## Errors

An error has an HTTP status and a body with the same codes as gRPC's `Error` message (without the `ERROR_` prefix):

```json
{"error": {"code": "COLLECTION_NOT_FOUND", "message": "collection not found", "request_id": "6f1c0e5a2b7d9c34"}}
```

| Status | Code | When |
| --- | --- | --- |
| 400 | `INVALID_ARGUMENT`, `INVALID_FILTER_LAYOUT`, `UNSUPPORTED_VERSION`, `SIZE_LIMIT` | Bad JSON, a missing or unknown field, or a spec the server refuses. |
| 401 | `UNAUTHENTICATED` | No key, or an unknown key. |
| 403 | `PERMISSION_DENIED` | The scope is too low, or the key is limited to another collection. |
| 404 | `NOT_FOUND`, `COLLECTION_NOT_FOUND` | Unknown route, or no such collection. |
| 405 | `METHOD_NOT_ALLOWED` | The route does not take this method. The `Allow` header lists the ones it takes. |
| 409 | `COLLECTION_EXISTS` | The name is taken. |
| 413 | `PAYLOAD_TOO_LARGE` | The body is over 64 KiB. The server refuses it before it reads the body. |
| 500 | `INTERNAL` | A server error. The message is fixed text. |
| 503 | `UNAVAILABLE`, `STORAGE_FULL` | The server is shutting down, or its disk is full. Retry later. |

The message is a fixed text. It never holds a collection name, an id, a value or a key.

## Request ids

Every response has an `X-Request-Id` header. The server uses the request's `X-Request-Id` if it is 1 to 64 characters from `A-Z a-z 0-9 . _ -`, and makes a random one otherwise. The id is in error bodies and on the log lines of the request. A log line has the route name, the status, the time and the first characters of the key's hash, never the collection name. See [Monitoring](../operations/monitoring.md#logs).

## Limits

The header block is at most 8 KiB and the body at most 64 KiB. A request has 5 seconds in total, including the TLS handshake. Bodies need `Content-Length`; chunked bodies are refused. One request per connection.
