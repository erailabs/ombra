# Server

The server is the `ombra_server` binary.

```text
ombra_server --data-dir D --listen HOST:PORT --tls-cert F --tls-key F [options]
ombra_server --create-api-key --scope admin|read|write --data-dir D [--collection NAME]
ombra_server --version
```

## Flags

| Flag | Default | Description |
| --- | --- | --- |
| `--data-dir D` | none, required | Directory for all server state. See [Backups](../../operations/backups.md). |
| `--listen HOST:PORT` | none, required | gRPC address. Port 0 picks a free port. |
| `--tls-cert F`, `--tls-key F` | none | PEM certificate and key, used by gRPC and the HTTP port. Give both or neither. |
| `--insecure` | off | Allows starting without TLS. Prints a warning. Only accepted on a loopback address, for gRPC and for the HTTP port. |
| `--no-auth` | off | Skips the API key check, for gRPC and the HTTP port. Prints a warning. Only accepted on a loopback address. |
| `--create-api-key` | off | Creates an API key, prints it once on stdout and exits. Needs `--scope` and `--data-dir`. |
| `--scope S` | none | Scope of the key to create: `admin`, `read` or `write`. |
| `--collection NAME` | none | With `--create-api-key`: limits the key to one collection. Only for `read` and `write` keys. See [API keys](../../security/api-keys.md). |
| `--log-format F` | `text` | `text` or `json`. See [Logs](#logs). |
| `--openfhe-version V` | the build's | OpenFHE version written into stored files and checked when reading them. |
| `--version` | | Prints the version and exits. |
| `--gpu-budget-mb N` | 4096 | GPU memory the block cache may use, in MiB. |
| `--host-budget-mb N` | 2048 | Host memory the block cache may use, in MiB. |
| `--session-budget-mb N` | 0 | GPU memory that all open searches together may hold for their expanded queries, in MiB. 0 means a quarter of `--gpu-budget-mb`. A search that does not fit fails with `RESOURCE_EXHAUSTED`. |
| `--compact-interval-ms N` | 60000 | How often the server drops blocks whose vectors are all deleted. 0 turns it off. |
| `--max-message-mb N` | 16 | Largest gRPC message the server accepts, in MiB (8 to 2000). Every call sends its data in parts of at most 4 MiB, or one column (under 1 MiB), so 16 is enough. A larger message is refused with `RESOURCE_EXHAUSTED`. |
| `--memory-quota-mb N` | 2048 | Memory that gRPC may use for buffered messages of all calls together, in MiB (at least 64). Keep it at least 32 times `--max-message-mb`. When it is used up, new messages wait or fail with `RESOURCE_EXHAUSTED`, which clients retry. |
| `--max-threads N` | 128 | Most threads gRPC may use (8 or more). |
| `--shutdown-grace-s N` | 30 | Seconds that running calls get to finish after SIGTERM or SIGINT. See [Shutdown](#shutdown). |
| `--max-sessions N` | 8 | Most Search streams open at once, all keys together. More fail with `RESOURCE_EXHAUSTED`. |
| `--max-sessions-per-key N` | 4 | Most Search streams open at once for one API key. |
| `--search-idle-s N` | 60 | A Search stream that the client leaves idle this many seconds ends. 0 turns the deadline off. |
| `--search-total-s N` | 600 | A Search stream lives at most this many seconds. 0 turns the deadline off. |
| `--max-rounds N` | 64 | Most rounds of one Search stream. |
| `--max-collections N` | 256 | Most collections the server holds. `CreateCollection` beyond it fails with `SIZE_LIMIT`. |
| `--max-cluster-count N` | 65536 | Largest `cluster_count` of a collection. The catalog keeps one row per cluster. |
| `--max-blobs N` | 1000000 | Most blobs per collection. |
| `--max-blob-mb N` | 16384 | Most blob bytes per collection, in MiB. |
| `--max-key-upload-mb N` | 2048 | Most bytes of one `UploadKeys` call, in MiB (1 to 4096). The server holds them in memory until the keys are checked. |
| `--rest-port N` | off | Starts the HTTP port: `/healthz`, `/readyz`, `/metrics` and the collection API, see [REST API](../rest/index.md). 0 picks a free port. |
| `--rest-host H` | `127.0.0.1` | IPv4 address of the HTTP port. It uses the same TLS certificate as gRPC. Without TLS or with `--no-auth` only a loopback address is accepted. |
| `--rest-workers N` | 4 | Threads that serve HTTP requests, so one slow client cannot block `/healthz`. |
| `--rest-max-conns N` | 32 | Most open HTTP connections. More are closed at once without an answer. |

Without `--tls-cert` and `--tls-key` the server refuses to start unless you pass `--insecure`. Without an API key file in the data directory it refuses to start unless you pass `--no-auth`. The key file is `api_keys.v1`, or `api_keys.v2` once a key is limited to a collection (see [API keys](../../security/api-keys.md)), and the server also refuses a file that group or others can read.

On start the server prints `ombra_server listening on HOST:PORT` to stdout, and with `--rest-port` also `ombra_server rest listening on HOST:PORT`.

## Shutdown

On the first SIGTERM or SIGINT the server does four things. It reports not ready at once: `/readyz` answers 503, while `/healthz` stays 200 because the process is still alive. It stops taking new calls, which fail with `UNAVAILABLE` and are retried by the client. It lets running calls finish for `--shutdown-grace-s` seconds, then cancels the rest. Then it exits with code 0. A call that is cancelled is not acknowledged, and the block it was still receiving is not stored. Blocks that were complete are stored, and a repeat of the call skips them. A second SIGTERM or SIGINT skips the wait and exits with code 1. Everything that was acknowledged is already on disk, and the next start removes what was unfinished.

When a limit is reached the server answers `RESOURCE_EXHAUSTED`, which the client retries: all search slots are in use (`--max-sessions`), too many key uploads are running, the GPU has no memory for one more search, or the disk is full. A full disk is also reported as `ERROR_STORAGE_FULL`. Nothing is stored in that case.

## Logs

The server writes one line per event to stderr. With `--log-format json` each line is a JSON object; the default is text. The fields are listed in [Monitoring](../../operations/monitoring.md#logs).

## Request ids

Every call has a request id, also over HTTP. The server uses the `x-request-id` metadata value of the call if it is 1 to 64 characters from `A-Z a-z 0-9 . _ -`, and makes a random 16-character one otherwise. It returns the id in the `x-request-id` response metadata (the `X-Request-Id` header over HTTP) and puts it on every log line of the call. The Python client sends a random id with each call, keeps it across retries (with `x-attempt` counting the tries) and sets it as `request_id` on the `OmbraError`. Quote it when you ask an operator about a failed call.

## API keys

An API key is an access token for the gRPC and HTTP ports. It is not an FHE key, and the server never sees the client's secret key. `--create-api-key` prints the token once. The file keeps only a salted SHA-256 hash, so a lost token cannot be recovered and you create a new one. The three scopes are described on the [gRPC API](../grpc/index.md) page. The HTTP collection API uses the same keys and the same rules. A key has a scope and, if you create it with `--collection`, one collection. Without `--collection` the key works on every collection. Send SIGHUP to the server to load new or revoked keys without a restart.

```bash
ombra_server --create-api-key --scope read --data-dir /data
```

The file format is on [Server files](../file-formats/server-files.md#api-key-file).
