# Docker image

An entrypoint script builds the `ombra_server` command line from environment variables. See [Docker and compose](../../operations/docker.md).

## Environment variables

All are optional. Arguments after the image name are passed on to `ombra_server`.

| Variable | Default | Description |
| --- | --- | --- |
| `OMBRA_LISTEN` | `0.0.0.0:50051` | gRPC address, as `--listen`. |
| `OMBRA_REST_PORT` | `8080` | HTTP port. Set it to `0` or empty to turn the HTTP port off. |
| `OMBRA_GPU_MB` | server default | As `--gpu-budget-mb`. |
| `OMBRA_HOST_MB` | server default | As `--host-budget-mb`. |
| `OMBRA_OPENFHE_VERSION` | the build's | As `--openfhe-version`. |
| `OMBRA_TLS_HOSTS` | `DNS:localhost,IP:127.0.0.1` | Subject alternative names of the self-signed certificate made on the first start. Read only while no certificate exists yet. |

The entrypoint always passes `--data-dir /data` and the TLS files in `/data/tls/`, and on first start makes a certificate and an admin API key if missing. Flags: [Server](server.md).
