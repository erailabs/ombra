# File formats

Every file Ombra writes starts with a magic string and a version number, so a program can tell what it is reading and refuse a version it does not know. All integers are little-endian. A reader never guesses: it refuses bad magic, an unknown version, a wrong kind, truncation and trailing bytes.

A format changes only with a version bump. The version of a file never depends on the version of the package that wrote it.

| Format | Written by | Magic | Version |
| --- | --- | --- | --- |
| [Envelope](envelope.md) | client, server | `{{ const("ombra.file_format.MAGIC").decode() }}` | {{ const("ombra.file_format.FORMAT_VERSION") }}, and {{ const("ombra.file_format.FORMAT_VERSION_SEEDED") }} for seeded ciphertexts |
| [Key file](key-file.md) | client | `{{ const("ombra.keyfile.MAGIC").decode() }}` | {{ const("ombra.keyfile.KEYFILE_VERSION") }}, and {{ const("ombra.keyfile.KEYFILE_VERSION_PIR") }} with the retrieval key pair |
| [Blob](blobs.md) | client | `{{ const("ombra.documents.MAGIC").decode() }}` | {{ const("ombra.documents.BLOB_VERSION") }} |
| [Segment](server-files.md#segment) | server | `OSEG` | {{ cpp("server/storage/include/ombra/storage/segment.hpp", "kSegmentVersion") }} |
| [Stored blob](server-files.md#stored-blob) | server | `OBLB` | {{ cpp("server/storage/include/ombra/storage/blob.hpp", "kBlobVersion") }} |
| [Write-ahead log](server-files.md#write-ahead-log) | server | `OWAL` | {{ cpp("server/storage/include/ombra/storage/wal.hpp", "kWalVersion") }} |
| [Catalog](server-files.md#catalog) | server | SQLite file, `schema_version` in its `meta` table | {{ cpp("server/storage/include/ombra/storage/catalog.hpp", "kCatalogSchemaVersion") }} |
| [Placement file](placement-file.md) | operator | first line `ombra-placement 1` | 1 |
| [Collection info](collection-info.md) | client | `OINF`, inside a record blob | 1 |
| [Migration state](migration-state.md) | client | JSON, field `format` is `ombra-migrate` | 1 |
| [API key file](server-files.md#api-key-file) | server | first line `OMBRA-API-KEYS v1` or `OMBRA-API-KEYS v2` | 1, and 2 for keys limited to a collection |

## Collection format

A collection is the set of the files above plus its catalog rows. The wire version is the `api_version` field, 1, stored with every collection spec. See the [gRPC API](../grpc/index.md).
