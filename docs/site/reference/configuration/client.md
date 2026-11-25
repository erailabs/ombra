# Client

Connection settings are arguments: `Client(address, api_key=..., root_certificates=..., insecure=...)`. No environment variable sets a server connection. See the [Python API](../python/index.md).

## Environment variables

- `OMBRA_LOCAL_THREADS` (default none): Local mode: threads for the local server's OpenFHE work.
- `OMBRA_SEEDED` (default `1`): Set to `0` to write ordinary ciphertexts instead of [seeded ones](../file-formats/envelope.md#seeded-ciphertext).
- `OMBRA_LOCAL_DEBUG` (default none): Set to `1` in local mode to print error tracebacks.

The command line tools read `OMBRA_API_KEY`, and `OMBRA_PASSPHRASE` and `OMBRA_TARGET_PASSPHRASE` for [`ombra-migrate`](../cli/ombra-migrate.md) and `OMBRA_KEY_PASSPHRASE` for [`ombra-keys`](../cli/ombra-keys.md), as the tools' pages describe.
