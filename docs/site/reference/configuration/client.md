# Client

The Python client takes its connection settings as arguments: `Client(address, api_key=..., root_certificates=..., insecure=...)`. It reads no environment variables for a server connection. See the [Python API](../python/index.md).

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `OMBRA_LOCAL_THREADS` | none | Local mode: the number of threads of the local server's OpenFHE work. |
| `OMBRA_SEEDED` | `1` | Set to `0` to write ordinary ciphertexts instead of [seeded ones](../file-formats/envelope.md#seeded-ciphertext). |
| `OMBRA_LOCAL_DEBUG` | none | Set to `1` in local mode to print the traceback of an error. For developers. |

The command line tools read `OMBRA_API_KEY`, and `OMBRA_PASSPHRASE` and `OMBRA_TARGET_PASSPHRASE` for [`ombra-migrate`](../cli/ombra-migrate.md) and `OMBRA_KEY_PASSPHRASE` for [`ombra-keys`](../cli/ombra-keys.md), as the tools' pages describe.
