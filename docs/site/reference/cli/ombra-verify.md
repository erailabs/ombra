# ombra_verify

Checks a server data directory for corruption. It runs on the CPU, needs no GPU and no OpenFHE, and prints counts and file names only.

```text
ombra_verify --data-dir DIR [--json] [--openfhe-version V]
```

| Flag | Default | Description |
| --- | --- | --- |
| `--data-dir DIR` | none, required | The data directory to check. |
| `--json` | off | Prints the result as one JSON object instead of text. |
| `--openfhe-version V` | none | Also requires every stored column and key file to carry this OpenFHE version, as the server does. Without it each file only has to agree with itself. |

| Exit code | Meaning |
| --- | --- |
| 0 | Clean. Warnings may be printed. |
| 1 | Something is corrupt. |
| 2 | Usage error, or `DIR` is not a readable directory. |

## What it checks

| Part | Check |
| --- | --- |
| Segment files | Header, version, CRC, that the file parses, and that its vector count, column count, CRC and cluster equal the catalog row. |
| Blob files | Header, version, CRC, kind and size against the catalog row. |
| Key files | Magic, format version, kind, preset, OpenFHE version and lengths. See the limit below. |
| Catalog | SQLite `integrity_check`, `quick_check` and `foreign_key_check`, the schema and API versions. |
| Cross-references | Every catalog row has its file. The vector rows of each block add up to the block's vector count. |
| Orphans | A file that no catalog row names is a warning. The server deletes such files at its next start. |
| Write-ahead log | Header, version and the CRC of every record. A torn tail is a warning. A damaged record that valid ones follow is corrupt, because the server refuses to start on it. |

A key file has no checksum in the [key file format](../file-formats/key-file.md), so a flipped byte in its payload is not found here. A truncated or extended file is. The same holds for the ciphertext payload inside a segment, but the segment's CRC covers it.

## Read-only

`ombra_verify` opens everything read-only. It does not run recovery, write, truncate or repair a file, and it takes no lock. It is safe to run while a server uses the directory, and it does not change what the server does.

The server takes no lock on the data directory, so `ombra_verify` cannot tell whether a server is running. On a live directory it can report a warning for a write in progress (a new file that the catalog does not list yet, a half-written log tail). It drops a "file missing" finding again when the catalog row is gone by the time it looks a second time, which happens when the server replaces or removes a block during the check. For a result you can rely on, check a backup or a stopped directory.

## Example

```text
$ ombra_verify --data-dir /data
checked: 2 collections, 4 segments, 2 blobs, 3 key files, 9 vectors, 3 log records
result: clean
```

```text
$ ombra_verify --data-dir /data
CORRUPT segment s0000000000000004.oseg: segment: CRC mismatch
checked: 2 collections, 4 segments, 2 blobs, 3 key files, 9 vectors, 3 log records
result: CORRUPT (1 problem(s))
```

In Docker:

```bash
docker compose exec ombra ombra_verify --data-dir /data
```
