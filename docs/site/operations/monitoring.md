# Monitoring

The server has a JSON metrics endpoint on the HTTP admin port and logs on stderr. There is no Prometheus or OpenTelemetry export; poll the JSON with a small exporter. Neither source carries plaintext, ids, collection names, scores or keys, only counts, byte sizes and timings.

## Health and readiness

```bash
curl --cacert server.crt https://localhost:8080/healthz   # {"status":"ok"}, no API key
curl --cacert server.crt https://localhost:8080/readyz    # {"status":"ready"}
```

The HTTP port uses the same TLS certificate as gRPC; drop `--cacert` only with `--insecure`. `/healthz` says the process is up, not that the GPU or data are fine. `/readyz` returns 503 with `{"status":"draining"}` from SIGTERM until exit. Use `/readyz` as the load balancer or Kubernetes readiness probe, so traffic stops before the server does, and `/healthz` for liveness: it stays 200 while draining, so the server is not restarted mid-shutdown.

## Metrics

```bash
curl --cacert server.crt -H "Authorization: Bearer $(cat admin_api_key)" \
    https://localhost:8080/metrics
```

The key must have the admin scope (missing key: 401, lower scope: 403, both counted). The body is JSON:

| Key | Meaning |
| --- | --- |
| `collections` | Number of collections. |
| `vectors_live`, `vectors_deleted` | Vectors in all collections, live and deleted but not yet compacted. |
| `blocks` | Number of stored blocks. |
| `rpc.<Method>.requests`, `.errors` | Calls since start for each gRPC method, and those that ended in an error. |
| `rpc.<Method>.latency_ms` | `sum`, `count` and `max`, in milliseconds. Divide `sum` by `count` for the mean. |
| `auth.unauthenticated`, `auth.permission_denied` | Calls refused for a missing or wrong key, or too low a scope. |
| `gpu_memory.used_bytes`, `gpu_memory.total_bytes` | GPU memory in use and in total. |
| `cache.hits`, `cache.misses`, `cache.evictions`, `cache.hit_rate` | Block cache counters since start, and hits over hits plus misses. |

Counters reset on restart. There are no latency percentiles. Rising `cache.misses` or `cache.evictions` with a low `hit_rate` means the cache budgets are too small, see [Supported GPUs](gpus.md#memory).

## Logs

Logs go to stderr (in Docker, `docker compose logs ombra`). Each line starts with `[ombra.server]`, then what happened and `name=value` pairs; `--log-format json` makes each line one JSON object. Examples with made-up numbers:

```text
[ombra.server] insert count=8192 bytes=104857600 ms=412.30
[ombra.server] refused reason=unauthenticated not_logged=0 request_id=4f1c0a9e22b7d310 rpc=Search
```

```json
{"ts":"2026-01-15T10:20:30.123Z","level":"info","msg":"insert","count":8192,"bytes":104857600,"ms":412.30,"request_id":"4f1c0a9e22b7d310","rpc":"Insert","key_id":"a1b2c3d4"}
```

| Field | Meaning |
| --- | --- |
| `ts`, `level` | UTC time with milliseconds, and `info`, `warn` or `error` (JSON only; in text, warnings and errors have a prefix). |
| `msg` | What happened. |
| `request_id` | The call's id, see [Request ids](../reference/configuration/server.md#request-ids). Lines that belong to no call have none. |
| `rpc` | The call name, for lines of a call. |
| `key_id` | The first 8 hex characters of the API key's stored hash. Missing when the key was not accepted. |
| `count`, `bytes`, `ms`, `reason`, `error` | Numbers and fixed reasons. `error` is the text of an internal error. |

Lines from storage and search code (`[ombra.storage]`, `[ombra.search]`) are plain text in both formats and have no request id. The server logs each write and search round, refused calls, REST requests (route name, status and time, never the collection name), compaction and internal errors. A line never contains an id, collection name, value or token. A key limited to another collection is refused with `reason=collection`.

On start the server prints the gRPC (and REST) address to stdout, and warns on stderr when it runs with `--insecure` or `--no-auth`. The Docker entrypoint prints one line when it makes a certificate or API key, never the key itself.

## What to watch

`/healthz`; error counts of `Search` and `Insert`; `auth.unauthenticated`, which climbs when a client has a wrong key or someone is probing; GPU memory against its total; and free disk space in the data volume, which the server does not report.
