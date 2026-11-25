# Router

`ombra_router` reuses the limits, API key file and log format of the server. See [Several nodes and a router](../../operations/routing.md).

```text
ombra_router --placement F --listen HOST:PORT --data-dir D --node-key-file F (--node-tls-ca F | --node-insecure) [options]
ombra_router --create-api-key --scope admin|read|write --data-dir D [--collection NAME]
ombra_router --version
```

## Flags

- `--placement F` (default none, required): The [placement file](../file-formats/placement-file.md). Re-read on `SIGHUP`.
- `--data-dir D` (default none, required): Holds the router's own API key file for its clients (`api_keys.v1` or `api_keys.v2`). Nothing else is stored.
- `--listen HOST:PORT` (default none, required): gRPC address for clients.
- `--tls-cert F`, `--tls-key F`, `--insecure`, `--no-auth` (default as for the server): Same rules as for [`ombra_server`](server.md#flags).
- `--node-key-file F` (default none): File with the `admin` API key the router presents to every node. Must not be readable by group or others.
- `--node-tls-ca F` (default none): PEM root certificate for node connections. Give exactly one of this and `--node-insecure`.
- `--node-insecure` (default off): Plain-text node connections, for tests on one machine. Prints a warning.
- `--max-sessions N`, `--max-sessions-per-key N` (default 8, 4): Open Search streams, all keys together and per client key. Enforced here because nodes see only the router's key.
- `--search-idle-s N`, `--search-total-s N`, `--max-rounds N` (default 60, 600, 64): Stream deadlines and the round cap, as for the server.
- `--max-message-mb N` (default 16): Largest gRPC message, for clients and nodes.
- `--shutdown-grace-s N` (default 30): As for the server.
- `--log-format F` (default `text`): `text` or `json`.

The router prints `ombra_router listening on HOST:PORT` once it is ready.
