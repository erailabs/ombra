# Server

```text
ombra_server --data-dir D --listen HOST:PORT --tls-cert F --tls-key F [options]
ombra_server --create-api-key --scope admin|read|write --data-dir D [--collection NAME]
ombra_server --version
```

## Flags

- `--data-dir D` (default none, required): Directory for all server state. See [Backups](../../operations/backups.md).
- `--listen HOST:PORT` (default none, required): gRPC address. Port 0 picks a free port.
- `--tls-cert F`, `--tls-key F` (default none): PEM certificate and key, used by gRPC and the HTTP port. Give both or neither.
- `--insecure` (default off): Starts without TLS, with a warning. Loopback address only, for gRPC and HTTP.
- `--no-auth` (default off): Skips the API key check, with a warning. Loopback address only.
- `--create-api-key` (default off): Creates an API key, prints it once and exits. Needs `--scope` and `--data-dir`.
- `--scope S` (default none): Scope of the key to create: `admin`, `read` or `write`.
- `--collection NAME` (default none): With `--create-api-key`: limits a `read` or `write` key to one collection. See [API keys](../../security/api-keys.md).
- `--log-format F` (default `text`): `text` or `json`. See [Logs](#logs).
- `--openfhe-version V` (default the build's): OpenFHE version written into stored files and checked when reading them.
- `--version` (default ): Prints the version and exits.
- `--gpu-budget-mb N` (default 4096): GPU memory the block cache may use, in MiB.
- `--host-budget-mb N` (default 2048): Host memory the block cache may use, in MiB.
- `--session-budget-mb N` (default 0): GPU memory for the expanded queries of all open searches, in MiB. 0 means a quarter of `--gpu-budget-mb`. A search that does not fit fails with `RESOURCE_EXHAUSTED`.
- `--compact-interval-ms N` (default 60000): How often blocks whose vectors are all deleted are dropped. 0 turns it off.
- `--max-message-mb N` (default 16): Largest gRPC message, in MiB (8 to 2000). 16 is enough, since calls send parts of at most 4 MiB. Larger messages fail with `RESOURCE_EXHAUSTED`.
- `--memory-quota-mb N` (default 2048): Memory gRPC may use for buffered messages of all calls, in MiB (at least 64; keep it at least 32 times `--max-message-mb`). When used up, new messages wait or fail with `RESOURCE_EXHAUSTED`, which clients retry.
- `--max-threads N` (default 128): Most threads gRPC may use (8 or more).
- `--shutdown-grace-s N` (default 30): Seconds running calls get to finish after SIGTERM or SIGINT. See [Shutdown](#shutdown).
- `--max-sessions N` (default 8): Most Search streams open at once, all keys together. More fail with `RESOURCE_EXHAUSTED`.
- `--max-sessions-per-key N` (default 4): Most Search streams open at once for one API key.
- `--search-idle-s N` (default 60): A Search stream idle this many seconds ends. 0 turns it off.
- `--search-total-s N` (default 600): Longest life of a Search stream. 0 turns it off.
- `--max-rounds N` (default 64): Most rounds of one Search stream.
- `--max-collections N` (default 256): Most collections the server holds. `CreateCollection` beyond it fails with `SIZE_LIMIT`.
- `--max-cluster-count N` (default 65536): Largest `cluster_count` of a collection. The catalog keeps one row per cluster.
- `--max-blobs N` (default 1000000): Most blobs per collection.
- `--max-blob-mb N` (default 16384): Most blob bytes per collection, in MiB.
- `--max-key-upload-mb N` (default 2048): Most bytes of one `UploadKeys` call, in MiB (1 to 4096). Held in memory until checked.
- `--rest-port N` (default off): Starts the HTTP port (`/healthz`, `/readyz`, `/metrics`, collection API; see [REST API](../rest/index.md)). 0 picks a free port.
- `--rest-host H` (default `127.0.0.1`): IPv4 address of the HTTP port, with the same TLS certificate as gRPC. Without TLS or with `--no-auth` only loopback is accepted.
- `--rest-workers N` (default 4): Threads serving HTTP requests.
- `--rest-max-conns N` (default 32): Most open HTTP connections. More are closed without an answer.

Without TLS files the server refuses to start unless you pass `--insecure`. Without an API key file in the data directory it refuses to start unless you pass `--no-auth`. The key file is `api_keys.v1`, or `api_keys.v2` once a key is limited to a collection (see [API keys](../../security/api-keys.md)). A key file that group or others can read is refused.

On start the server prints `ombra_server listening on HOST:PORT` (and with `--rest-port` also `ombra_server rest listening on HOST:PORT`) to stdout.

## Shutdown

On the first SIGTERM or SIGINT the server:

1. Reports not ready: `/readyz` answers 503, `/healthz` stays 200.
2. Stops taking new calls, which fail with `UNAVAILABLE` (the client retries).
3. Lets running calls finish for `--shutdown-grace-s` seconds, then cancels the rest.
4. Exits with code 0.

A cancelled call is not acknowledged and its unfinished block is not stored; complete blocks are stored and a repeat skips them. A second signal skips the wait and exits with code 1. Everything acknowledged is on disk, and the next start removes what was unfinished.

The server answers `RESOURCE_EXHAUSTED` (which the client retries) when all search slots are in use (`--max-sessions`), too many key uploads run, the GPU has no memory for one more search, or the disk is full. A full disk is also `ERROR_STORAGE_FULL`, and nothing is stored.

## Logs

One line per event on stderr, text or (`--log-format json`) JSON objects. Fields: [Monitoring](../../operations/monitoring.md#logs).

## Request ids

Every call, also over HTTP, has a request id. The server uses the call's `x-request-id` metadata if it is 1 to 64 characters from `A-Z a-z 0-9 . _ -`, else a random 16-character one. It returns the id in the `x-request-id` response metadata (`X-Request-Id` header over HTTP) and puts it on every log line of the call. The Python client sends a random id per call, keeps it across retries (`x-attempt` counts tries) and sets it as `request_id` on the `OmbraError`.

## API keys

An API key is an access token for the gRPC and HTTP ports, not an HE key. `--create-api-key` prints it once; the file keeps only a salted SHA-256 hash. Without `--collection` a key works on every collection. SIGHUP loads new or revoked keys without a restart. File format: [Server files](../file-formats/server-files.md#api-key-file). Scopes: [gRPC API](../grpc/index.md).

```bash
ombra_server --create-api-key --scope read --data-dir /data
```
