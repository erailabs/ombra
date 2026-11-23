# Server files

The server keeps everything in its data directory, laid out as in [Backups](../../operations/backups.md). Clients never read these files, but the formats are versioned the same way.

| File | Magic | Version |
| --- | --- | --- |
| [Segment](#segment) | `OSEG` | {{ cpp("server/storage/include/ombra/storage/segment.hpp", "kSegmentVersion") }} |
| [Record file](#record-file) | `OREC` | {{ cpp("server/storage/include/ombra/storage/record_column.hpp", "kRecordFileVersion") }} |
| [Record column](#record-column) | `ORCL` | {{ cpp("server/storage/include/ombra/storage/record_column.hpp", "kRecordColumnVersion") }} |
| [Stored blob](#stored-blob) | `OBLB` | {{ cpp("server/storage/include/ombra/storage/blob.hpp", "kBlobVersion") }} |
| [Write-ahead log](#write-ahead-log) | `OWAL` | {{ cpp("server/storage/include/ombra/storage/wal.hpp", "kWalVersion") }} |
| [Catalog](#catalog) | SQLite, `schema_version` in `meta` | {{ cpp("server/storage/include/ombra/storage/catalog.hpp", "kCatalogSchemaVersion") }} |
| [API key file](#api-key-file) | first line `OMBRA-API-KEYS v1` | 1 |
| [API key file, version 2](#api-key-file-version-2) | first line `OMBRA-API-KEYS v2` | 2 |

## Segment

`segments/<name>.oseg`: one block of one cluster, immutable once written. Magic `OSEG`, a 2-byte version, a CRC32 of the body, then the cluster number, the ids and the column envelopes. Each column is a complete [envelope](envelope.md) of kind ciphertext. Source: `server/storage/include/ombra/storage/segment.hpp`.

## Record file

`records/<segment stem>.orec`: the optional record column of one block, named after the block's segment (`s<16 hex>.oseg` has `s<16 hex>.orec`). A block has at most one, and a block without one has no file. It is written once, before its segment, and never changes.

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | Magic `OREC`. |
| 4 | 2 | Container version, u16, currently {{ cpp("server/storage/include/ombra/storage/record_column.hpp", "kRecordFileVersion") }}. |
| 6 | 4 | CRC32 of the column, u32. |
| 10 | n | The [record column](#record-column), stored verbatim. |

All numbers are little endian. Source: `server/storage/include/ombra/storage/record_column.hpp`.

## Record column

The sealed re-rank rows of one block: one row per id of the block, in id order. The client builds it and sends it with the block. The server never interprets a row. The rows are bytes that the client encrypted, and the server only checks the header and the length. What a row holds is on [Documents and blobs](../../how-it-works/documents.md#record-rows-for-private-retrieval).

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | Magic `ORCL`. |
| 4 | 2 | Version, u16, currently {{ cpp("server/storage/include/ombra/storage/record_column.hpp", "kRecordColumnVersion") }}. |
| 6 | 2 | Reserved, u16, must be 0. |
| 8 | 4 | `row_size`, u32: bytes per row, nonzero and a multiple of 64. |
| 12 | 4 | `n_rows`, u32. |
| 16 | `n_rows` x `row_size` | The rows. |

The length must be exactly 16 + `n_rows` x `row_size`, and a column is at most 1 GiB (`kMaxRecordColumnBytes`). A reader refuses another magic or version, a nonzero reserved field, a bad `row_size` or a wrong length. [`ombra_verify`](../cli/ombra-verify.md) also checks that `n_rows` equals the number of slots of the block.

## Stored blob

`blobs/<name>.oblob`: magic `OBLB`, a 2-byte version, a CRC32 of the body, then the blob kind and the payload, which is the client's [blob](blobs.md) and is never parsed. Source: `server/storage/include/ombra/storage/blob.hpp`.

## Write-ahead log

`wal.log`: magic `OWAL`, a 2-byte version, then records of a length, a CRC32 and a body. Records are insert intent, insert commit and delete. On start the server replays the log: an insert that was acknowledged is rolled forward, one that was not is discarded. Source: `server/storage/include/ombra/storage/wal.hpp`.

## Catalog

`catalog.db`: a SQLite database with the collections, aliases, segments, blobs and tombstones. Its `meta` table holds `schema_version` and `api_version`. The server upgrades an older schema when it opens it, in one transaction, and refuses a newer one. The current schema version is {{ cpp("server/storage/include/ombra/storage/catalog.hpp", "kCatalogSchemaVersion") }}.

Schema history:

- Schema 2 added the blob table.
- Schema 3 added the `aliases` table: `name` (primary key), `target` (a collection), `previous` (the target before the last switch, or null) and `switched_at` (unix seconds). Alias names and collection names are one namespace.

An older server cannot open a schema 3 catalog, so going back means restoring a backup. [`ombra_verify`](../cli/ombra-verify.md) accepts schema 1 and 2 as "migrates at the next start" and checks that every alias points at a collection.

## API key file

`api_keys.v1`: text, mode 0600. The first line is `OMBRA-API-KEYS v1`, then one line per key with a salt, a salted SHA-256 hash and a scope. It holds no token.

## API key file, version 2

`api_keys.v2`: the same, with an optional fourth field on a line, the collection the key is limited to. The first line is `OMBRA-API-KEYS v2`. A line is `<salt, 32 hex> <hash, 64 hex> <scope> [<collection>]`, separated by single spaces. The collection is 1 to 128 printable ASCII characters without a space.

An `admin` line with a collection, a fifth field, a bad hex string, an unknown scope or a file that group or others can read is refused, and so is a v1 file with a fourth field.

The server reads both versions: it opens `api_keys.v2` if it exists, else `api_keys.v1`. It writes v1 until you create the first key with `--collection`. Then it writes v2 with the old keys copied unchanged and renames the old file to `api_keys.v1.migrated`. A v2 file is never turned back into v1. Version 1 files keep working with no change. See [API keys](../../security/api-keys.md).
