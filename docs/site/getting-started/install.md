# Install

The client is a Python package. The server is a Docker image, needed only for GPU speed.

## The client

```bash
pip install "ombra[local]"
```

The `local` extra adds local mode, which runs a CPU server inside your process so you can try Ombra without a GPU. It installs `ombra-openfhe`, our build of openfhe-python on OpenFHE 1.6.1. Plain `pip install ombra` is enough when you only talk to a server.

Python 3.11, 3.12 and 3.13 are supported. Wheels exist for these platforms, so no compiler is needed:

| Platform | Architectures |
| --- | --- |
| Linux (manylinux_2_28) | x86_64, aarch64 |
| macOS 15 or newer | arm64, x86_64 |

There is no Windows build. Local mode needs about 1 GB of free disk space and a few GB of memory; a collection of six short texts writes a few hundred MB.

The quickstart also uses an embedding model with 384, 768 or 1536 dimensions. The examples use `sentence-transformers`:

```bash
pip install sentence-transformers
python -c "import ombra; print(ombra.__version__)"   # check the install
```

## The server

The server needs a host with a supported NVIDIA GPU (see [GPUs](../operations/gpus.md)) and Docker with the NVIDIA container toolkit. Start it from the compose file in the repository:

```bash
docker compose up -d
```

The data lives in a volume and survives restarts. [Docker](../operations/docker.md) covers the image, volume, TLS and API keys. With no GPU, stay with local mode.

Next: [Quickstart](quickstart.md)
