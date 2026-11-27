# ombra_verify

Checks a server data directory for corruption. CPU only; prints counts and file names only.

```text
ombra_verify --data-dir DIR [--json] [--openfhe-version V]
```

| Flag | Default | Description |
| --- | --- | --- |
| `--data-dir DIR` | none, required | The data directory to check. |
| `--json` | off | Prints the result as one JSON object instead of text. |
| `--openfhe-version V` | none | Also requires every column and key file to carry this OpenFHE version, as the server does. Without it each file only has to agree with itself. |

| Exit code | Meaning |
| --- | --- |
| 0 | Clean. Warnings may be printed. |
| 1 | Something is corrupt. |
| 2 | Usage error, or `DIR` is not a readable directory. |

## What it checks

| Part | Check |
| --- | --- |
| Segment files | Header, version, CRC, that the file parses, and that its vector count, column count, CRC and cluster equal the catalog row. |
| Record files | `records/*.orec`, one per block at most, named after its segment. Header, version and CRC, that the column parses, and that its row count equals the block's slot count. A block with no record file is fine. A record file that no block names is a warning. |
| Blob files | Header, version, CRC, kind and size against the catalog row. |
| Key files | Magic, format version, kind, preset, OpenFHE version and lengths. See the limit below. |
| Catalog | SQLite `integrity_check`, `quick_check` and `foreign_key_check`, the schema and API versions. |
| Cross-references | Every catalog row has its file. The vector rows of each block add up to the block's vector count. |
| Orphans | A file that no catalog row names (a record file: that no block's segment names) is a warning. The server deletes such files at its next start. |
| Write-ahead log | Header, version and the CRC of every record. A torn tail is a warning. A damaged record that valid ones follow is corrupt, because the server refuses to start on it. |

A key file has no checksum in the [key file format](../file-formats/key-file.md), so a flipped payload byte is not found here; a truncated or extended file is.

## Read-only

Everything is opened read-only: no recovery, write, truncate, repair or lock. It is safe to run while a server uses the directory. The server takes no lock, so the tool cannot tell whether one runs. On a live directory it can warn about a write in progress (a new file the catalog does not list yet, a half-written log tail), and it drops a "file missing" finding if the catalog row is gone on a second look. For a reliable result, check a backup or a stopped directory.

## Example

```text
checked: 2 collections, 4 segments, 2 blobs, 3 key files, 9 vectors, 3 log records
result: clean
```

```text
CORRUPT segment s0000000000000004.oseg: segment: CRC mismatch
result: CORRUPT (1 problem(s))
```
