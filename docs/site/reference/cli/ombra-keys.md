# ombra-keys

Backs up and checks key files. `python -m ombra.keys` is the same. It needs OpenFHE for `check`. It runs where your application runs, and it never prints a key or the passphrase.

```text
ombra-keys backup KEYFILE DESTINATION [--force] [--check-passphrase]
ombra-keys check KEYFILE [--tag TAG | --server HOST:PORT --collection NAME [--insecure]]
```

| Flag | Meaning |
| --- | --- |
| `--force` | Replace an existing destination. Without it, `backup` refuses. |
| `--check-passphrase` | `backup` asks for the passphrase and opens the copy. Without a passphrase source, `backup` checks the file structure and that the copy equals the source. |
| `--passphrase-env NAME` | Read the passphrase from this environment variable. `OMBRA_KEY_PASSPHRASE` is used if set and no other source is given. |
| `--passphrase-file PATH` | Read it from the first line of a file. |
| `--passphrase-fd N` | Read it from an open file descriptor. |
| `--tag TAG` | Compare the key file's key tag with `TAG`, offline. |
| `--server`, `--collection` | Compare it with the key tag of a collection on a server. The API key comes from the environment variable `OMBRA_API_KEY`. |
| `--insecure` | No TLS, for a loopback test server. |

With no passphrase source, `check` prompts. There is no option that takes the passphrase itself. Exit code 0 is OK, 1 a failed check and 2 bad usage.

The same functions are in `ombra.keyfile`: `backup_key_file` and `check_key_file`.

## Example

```bash
ombra-keys backup my.key /mnt/offsite/my.key.2026-01-15 --check-passphrase
ombra-keys check /mnt/offsite/my.key.2026-01-15 --server ombra.example.com:443 --collection docs
```

The [Backups](../../operations/backups.md) page says when to run each.
