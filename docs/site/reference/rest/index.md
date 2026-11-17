# REST API

The HTTP port has a small API to manage collections. It is for operators and scripts. It does not carry vectors, queries, documents, ids or keys: those go over [gRPC](../grpc/index.md), and so do the HE keys of a collection (`UploadKeys`) and every insert and search.

## Base URL and port

Turn the port on with `--rest-port`. The Docker image does it at 8080. The port uses the same TLS certificate as gRPC and the same API key file. By default it listens on `127.0.0.1`. See [Configuration](../configuration/server.md) for `--rest-host`, `--rest-workers` and `--rest-max-conns`.

The base URL is `http://127.0.0.1:8080` without TLS, or `https://<host>:<port>` with it. Without TLS (`--insecure`) the port accepts only a loopback address. Behind a proxy or on another address, use TLS. A key sent over plain HTTP can be read by anyone on the path.

Names in the path are percent-decoded. A name with a `/`, a control character or more than 128 bytes is an unknown route. Answers are JSON.

## Authentication

Send an API key as `Authorization: Bearer <key>`. The key, its scope and its optional collection are the ones of gRPC, and each route needs the scope of the gRPC method that does the same work.

A key limited to one collection sees only that collection in the list. For any other name it gets `403`, whether the collection exists or not. It can create only a collection with its own name. A request without a valid key gets `401`. Only `--no-auth` turns the check off, and the server accepts that only on a loopback address, as it does for gRPC. See [API keys](../../security/api-keys.md).

## Routes

| Route | Same as | Scope | Page |
| --- | --- | --- | --- |
| `GET /v1/collections` | `ListCollections` | read | [List collections](list-collections.md) |
| `GET /v1/collections/{name}` | `GetCollection` | read | [Get a collection](get-collection.md) |
| `POST /v1/collections` | `CreateCollection` | write | [Create a collection](create-collection.md) |
| `DELETE /v1/collections/{name}` | `DropCollection` | admin | [Drop a collection](drop-collection.md) |

The port also has `/healthz` and `/readyz` (no key) and `/metrics` (admin key). See [Monitoring](../../operations/monitoring.md).

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

Every response has an `X-Request-Id` header. The server uses the request's `X-Request-Id` if it is 1 to 64 characters from `A-Z a-z 0-9 . _ -`, and makes a random one otherwise. The id is in error bodies and on the log lines of the request. A log line has the route name, the status, the time and the first characters of the key's hash, never the collection name. See [Monitoring](../../operations/monitoring.md#logs).

## Limits

The header block is at most 8 KiB and the body at most 64 KiB. A request has 5 seconds in total, including the TLS handshake. Bodies need `Content-Length`; chunked bodies are refused. One request per connection.
