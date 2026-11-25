# REST API

A small API to manage collections, for operators and scripts. It carries no vectors, queries, documents, ids or keys: those, and every insert and search, go over [gRPC](../grpc/index.md).

## Base URL and port

Turn the port on with `--rest-port` (the Docker image uses 8080). It uses the TLS certificate and API key file of gRPC and listens on `127.0.0.1` by default. See [Configuration](../configuration/server.md) for `--rest-host`, `--rest-workers` and `--rest-max-conns`.

The base URL is `http://127.0.0.1:8080` without TLS, or `https://<host>:<port>`. Without TLS (`--insecure`) only a loopback address is accepted; on any other address use TLS, since a key sent over plain HTTP can be read on the path.

Names in the path are percent-decoded. A name with a `/`, a control character or more than 128 bytes is an unknown route. Answers are JSON.

## Authentication

Send `Authorization: Bearer <key>`. Keys, scopes and collection limits are those of gRPC, and each route needs the scope of the matching gRPC method.

A key limited to one collection sees only that collection in the list, gets `403` for any other name (whether it exists or not) and can create only a collection with its own name. No valid key gives `401`. `--no-auth` turns the check off, on a loopback address only. See [API keys](../../security/api-keys.md).

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

- 400 `INVALID_ARGUMENT`, `INVALID_FILTER_LAYOUT`, `UNSUPPORTED_VERSION`, `SIZE_LIMIT`: Bad JSON, a missing or unknown field, or a spec the server refuses.
- 401 `UNAUTHENTICATED`: No key, or an unknown key.
- 403 `PERMISSION_DENIED`: The scope is too low, or the key is limited to another collection.
- 404 `NOT_FOUND`, `COLLECTION_NOT_FOUND`: Unknown route, or no such collection.
- 405 `METHOD_NOT_ALLOWED`: The route does not take this method. The `Allow` header lists the ones it takes.
- 409 `COLLECTION_EXISTS`: The name is taken.
- 413 `PAYLOAD_TOO_LARGE`: The body is over 64 KiB. The server refuses it before it reads the body.
- 500 `INTERNAL`: A server error. The message is fixed text.
- 503 `UNAVAILABLE`, `STORAGE_FULL`: The server is shutting down, or its disk is full. Retry later.

The message is fixed text, never a collection name, id, value or key.

## Request ids

Every response has an `X-Request-Id` header: the request's own if it is 1 to 64 characters from `A-Z a-z 0-9 . _ -`, else a random one. It is in error bodies and log lines. A log line has the route name, status, time and the first characters of the key's hash, never the collection name. See [Monitoring](../../operations/monitoring.md#logs).

## Limits

Header block at most 8 KiB, body at most 64 KiB. A request has 5 seconds in total, including the TLS handshake. Bodies need `Content-Length`; chunked bodies are refused. One request per connection.
