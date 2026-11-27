# Docker and compose

The server runs from a Docker image. You need Docker, an NVIDIA GPU from the [supported list](gpus.md) and the NVIDIA container toolkit.

## Images

| Image | What it is |
| --- | --- |
| `ghcr.io/erailabs/ombra-server` | The GPU server, built for the five architectures of [Supported GPUs](gpus.md), on the CUDA 12.6 runtime. Runs as the unprivileged user `ombra`. |
| `ghcr.io/erailabs/ombra-cpu` | The Python client with local mode, for tests and development. It has no `ombra_server`. |

## Compose

The `docker-compose.yml` at the root of the repository (comments omitted):

```yaml
name: ombra

services:
  ombra:
    image: ghcr.io/erailabs/ombra-server:${OMBRA_VERSION:-0.1.0}
    build:
      context: .
      dockerfile: docker/Dockerfile.server
    ports:
      - "50051:50051"  # gRPC
      - "127.0.0.1:8080:8080"  # REST admin and metrics, on this machine only
    volumes:
      - ombra-data:/data
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    stop_grace_period: 40s

volumes:
  ombra-data:
```

## First start

```bash
docker compose up -d
docker compose exec ombra cat /data/admin_api_key
docker compose cp ombra:/data/tls/server.crt ./server.crt
```

On the first start the entrypoint makes two things in the `/data` volume and reuses them later: a self-signed TLS certificate in `/data/tls/`, valid for 825 days, for `localhost` and `127.0.0.1`, and one admin API key in `/data/admin_api_key`, readable only by the server's user. The key is never printed to the log. The volume stores only its salted hash in `api_keys.v1`, so if you delete `admin_api_key` you cannot get the token back.

To reach the server by another name, set `OMBRA_TLS_HOSTS` before the first start, for example `OMBRA_TLS_HOSTS=DNS:ombra.example.com`. It is ignored once a certificate exists; to change it later, delete `/data/tls/` in the volume and restart.

Connect with the key and the certificate as the trusted root:

```python
client = Client(
    "localhost:50051",
    api_key=Path("admin_api_key").read_text().strip(),
    root_certificates=Path("server.crt").read_bytes(),
)
```

An admin key can do everything, including dropping collections. For an application, make a read or write key, see [Configuration](../reference/configuration/server.md#api-keys).

## Ports

Port 50051 is gRPC. Port 8080 is the HTTP admin port, with `/healthz`, `/readyz`, `/metrics` and the [collection API](../reference/rest/index.md). The compose file publishes it on `127.0.0.1` only. Change the mapping only if you put something in front of it that restricts access. To turn it off, set `OMBRA_REST_PORT=0` in the service's `environment`.

Environment variables for the cache budgets, the listen address and the certificate names are in [Configuration](../reference/configuration/docker.md). Anything in `command:` is passed to `ombra_server`.

## Data and stopping

Everything the server stores is in the `ombra-data` volume. `docker compose down` keeps it, `docker compose down -v` deletes it with all collections. See [Backups](backups.md). On a stop, Docker sends SIGTERM. The server reports not ready, lets running calls finish for `--shutdown-grace-s` seconds (30 by default), and exits. Set `stop_grace_period` above that value, as the compose file does (40 seconds), or Docker kills the server early. A killed server loses nothing that was acknowledged, but running calls fail.

## Building the image yourself

```bash
docker build -f docker/Dockerfile.server -t ombra-server .
```

The build compiles OpenFHE 1.6.1 and the CUDA code for the five architectures, and takes about an hour. `docker compose build` does the same.
