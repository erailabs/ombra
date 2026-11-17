# ombra_backup

Copies a server data directory, then verifies the copy. It runs on the CPU, needs no GPU and no OpenFHE, and prints counts and file names only.

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

The copy is the state of one instant, and it is safe to take while the server runs. The server does not need to cooperate and `DIR` is not changed. The [Backups](../../operations/backups.md) page says how to use it and why the copy is consistent. After copying, `ombra_backup` runs the same checks as [`ombra_verify`](ombra-verify.md) on the copy and fails if any file is corrupt.

The copy holds `catalog.db`, `segments/`, `blobs/`, `keys/` and the API key file (`api_keys.v1` or `api_keys.v2`) when present. It has no `wal.log`, no `tls/` and no `admin_api_key`. The server creates a new log on its first start from the copy.

## Example

```bash
ombra_backup --data-dir /var/lib/ombra --out /backups/ombra-2026-01-15
```

In Docker, run the tool in a second container on the same volume and write to a folder on the host. The folder must be writable by user 10001, the user of the image.

```bash
mkdir -p backup && sudo chown 10001 backup
docker run --rm -v ombra_ombra-data:/data -v "$PWD/backup":/backup \
    --entrypoint ombra_backup ghcr.io/erailabs/ombra-server:0.1.0 \
    --data-dir /data --out /backup/ombra-2026-01-15
```

See [Backups](../../operations/backups.md#online-backup) for the volume name and for restoring.
