# Backups

Two things need a backup: the server's data directory (the encrypted collections) and the client's key file (the only key that can read them).

## The key file

Back up each collection's key file and passphrase on the client side, somewhere other than the server. Without the key file nobody can decrypt the data, and there is no recovery. See the [key file format](../reference/file-formats/key-file.md).

`ombra-keys` (also `python -m ombra.keys`) copies and checks a key file. It never takes the passphrase as an argument, because arguments show in the process list: use a prompt, an environment variable, a file or a file descriptor.

```bash
# Copy atomically (temp file, fsync, rename), mode 0600. Refuses to replace a file without --force.
ombra-keys backup my.key /mnt/offsite/my.key.2026-01-15 --check-passphrase

# Compare the key tag with the one on the server, or offline with a tag you wrote down
OMBRA_KEY_PASSPHRASE=... OMBRA_API_KEY=... \
  ombra-keys check /mnt/offsite/my.key.2026-01-15 --server ombra.example.com:443 --collection docs
ombra-keys check /mnt/offsite/my.key.2026-01-15 --tag "$KEY_TAG"
```

`check` opens the file, checks that the secret and public keys work and share a key tag, and prints `OK` with the tag (not secret: the server stores it). Run it on every backup and after each restore test. Exit code 0 is OK, 1 a failed check. See [`ombra-keys`](../reference/cli/ombra-keys.md).

## The server data directory

In Docker this is the `ombra-data` volume at `/data`. Otherwise it is `--data-dir`.

| Path | Contents |
| --- | --- |
| `catalog.db` | Collections, blocks, ids, tombstones and where every file is. |
| `wal.log` | The write-ahead log. |
| `segments/` | One file per block: the encrypted vectors of a cluster. |
| `records/` | One file per block with a record column (sealed rows for private record retrieval). Optional. |
| `blobs/` | One file per encrypted blob: records and documents. |
| `keys/` | The public evaluation keys of each collection. |
| `api_keys.v1` or `api_keys.v2` | Salted hashes of the API keys, see [API keys](../security/api-keys.md). |
| `tls/`, `admin_api_key` | The self-signed certificate and key, and the admin token (Docker image). |

Nothing here is plaintext or can decrypt anything. A stolen backup gives the same access as a stolen server disk, no more. It does contain the TLS private key and the admin token, so treat it as a secret.

### Online backup

`ombra_backup` copies the directory as of one instant while the server runs, without changing it. Prefer it to stop-and-copy.

```bash
mkdir -p backup && sudo chown 10001 backup
docker run --rm -v ombra_ombra-data:/data -v "$PWD/backup":/backup \
    --entrypoint ombra_backup ghcr.io/erailabs/ombra-server:0.1.0 \
    --data-dir /data --out /backup/ombra-2026-01-15
```

Outside Docker: `ombra_backup --data-dir /var/lib/ombra --out /backups/ombra-2026-01-15`. The output directory must not exist or be empty, and must not lie inside the data directory. At the end it checks every copied file, as [`ombra_verify`](../reference/cli/ombra-verify.md) does, and fails (leaving the output empty) if one is corrupt. The volume name has the compose project name as a prefix (`ombra_ombra-data` for the shipped file); check with `docker volume ls`.

The copy lacks the write-ahead log, `tls/` and `admin_api_key`. Copy the last two yourself if you want them. Without `tls/` the image makes a new certificate on first start and clients need the new one. Without `admin_api_key` you cannot read the admin token again, see [Docker and compose](docker.md).

!!! deep-dive "Why the copy is consistent while the server writes"
    SQLite copies the catalog in one read snapshot. The server writes a file completely before the catalog row that names it and never reuses a file name, so every file the copied catalog names existed at that instant and cannot change. `ombra_backup` opens them all right after the snapshot (an open file stays readable even if removed later). If one is already gone it takes a new snapshot, up to 5 times, then fails and you run it again. The write-ahead log is not copied: a write is acknowledged only after the catalog has it, so every acknowledged write is in the copy, and one in progress may or may not be, as after a crash.

### Backup with the server stopped

This also copies `tls/` and `admin_api_key`. A plain copy of a running server's directory is not safe; use `ombra_backup`, stop the server, or snapshot the whole filesystem (LVM, ZFS, a cloud disk snapshot).

```bash
docker compose stop ombra
docker run --rm -v ombra_ombra-data:/data -v "$PWD":/backup busybox \
    tar czf /backup/ombra-data.tar.gz -C /data .
docker compose start ombra
```

### Checking a data directory

`ombra_verify` re-checks every file and the catalog without changing anything. Run it on a new backup, on a restored copy before starting the server on it, and now and then on the live directory.

```bash
docker run --rm -v ombra_ombra-data:/data --entrypoint ombra_verify \
    ghcr.io/erailabs/ombra-server:0.1.0 --data-dir /data
```

Exit code 0 is clean, 1 corrupt, 2 usage error. It prints counts and file names only. On a live directory a write in progress can show as a warning. If a file is corrupt, restore from the last backup: the server refuses to read it. See [`ombra_verify`](../reference/cli/ombra-verify.md).

### Disk full

When the disk or quota fills, a write fails (an internal error over gRPC for now) and the server stays up and consistent. The call either did not happen or happened completely, and files it started are removed. Writes work again as soon as there is space, and the client can resend. After a restart the server finishes or drops any write in progress and deletes leftover files. Searches are not affected. Watch the free space of the data volume.

### Restoring

```bash
docker compose down
docker volume rm ombra_ombra-data
docker run --rm -v ombra_ombra-data:/data -v "$PWD":/backup busybox \
    tar xzf /backup/ombra-data.tar.gz -C /data
docker compose up -d
```

On start the server checks every file against its version and CRC and replays the write-ahead log: an acknowledged insert is kept, one that was not is dropped. A restored copy must come from the same or an older Ombra version than the one that reads it, see [Upgrades](upgrades.md). Files belong to the user `ombra` (uid 10001); `tar` keeps the stored owners.

A backup is one moment: vectors inserted after it are not in it, and deleted vectors come back. Ombra does not replicate the server or back up on its own.
