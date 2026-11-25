# ombra_backup

Copies a server data directory, then verifies the copy. CPU only; prints counts and file names only.

```text
ombra_backup --data-dir DIR --out DIR2
```

| Flag | Default | Description |
| --- | --- | --- |
| `--data-dir DIR` | none, required | The data directory to copy. |
| `--out DIR2` | none, required | Where to put the copy. It must not exist or be empty, and must not lie inside `DIR`. |

| Exit code | Meaning |
| --- | --- |
| 0 | The copy is done and was verified. |
| 1 | The backup failed. `DIR2` is empty again. |
| 2 | Usage error. |

The copy is the state of one instant and is safe to take while the server runs; `DIR` is not changed. See [Backups](../../operations/backups.md). After copying, the tool runs the checks of [`ombra_verify`](ombra-verify.md) on the copy and fails if any file is corrupt.

The copy holds `catalog.db`, `segments/`, `blobs/`, `keys/` and the API key file (`api_keys.v1` or `api_keys.v2`) when present. It has no `wal.log`, `tls/` or `admin_api_key`; the server creates a new log on first start.

## Example

```bash
ombra_backup --data-dir /var/lib/ombra --out /backups/ombra-2026-01-15
```

In Docker, run it in a second container on the same volume; the output folder must be writable by user 10001. See [Backups](../../operations/backups.md#online-backup).
