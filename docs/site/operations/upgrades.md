# Upgrades

Every stored file and message carries a version, and a reader refuses a version it does not know. A mismatch gives a clear error at start or on the first call, not silent misreading.

## Upgrading the server

Take a [backup](backups.md) of the data volume with the server stopped, and read the new version's entry in `CHANGELOG.md`, which lists any format change. With compose, set the new tag and recreate the container. The data volume stays.

```bash
OMBRA_VERSION=0.2.0 docker compose pull
OMBRA_VERSION=0.2.0 docker compose up -d
```

The version is an example. Without `OMBRA_VERSION` the compose file uses the version it shipped with.

On start the server upgrades an older catalog in place, in one transaction: schema 1 and 2 become schema 3, which adds the table of [aliases](migration.md#aliases). An older server cannot open a schema 3 catalog, and a newer schema makes the server refuse to start with `unsupported schema_version`. Segments, blobs, the write-ahead log and the API key file are read as they are, and an unknown version is refused. The certificate and API keys in the volume are reused. You cannot go back by starting an older image on an upgraded volume: restore the backup.

## Upgrading the client

Install the new wheel with `pip install -U ombra`. Client and server talk through the gRPC package `ombra.v1`. An incompatible change will be a new package, `ombra.v2`: an old client keeps working against a server that still serves `ombra.v1`, and a new client reports a version error against one that does not.

The OpenFHE version matters more than the Ombra version. Every envelope carries the OpenFHE version that wrote it (1.6.1 for the 0.1 series) and a reader refuses another, so a collection can only be used by a client and server built with the same OpenFHE. The `ombra[grpc]` and `ombra[local]` extras pin the matching `openfhe` package.

## What the tests check

`make test-upgrade` (marker `upgrade`) runs on the CPU build of the server, so Search is checked only as far as it starts and fails with the "without GPU support" error. The previous release is the highest `v*` tag below the commit under test; the tests build its CPU server and client, and skip while no release tag exists.

- **Old client, new server**, and **new client, old server.** A session creates a collection, uploads keys, inserts, deletes, replaces a block, writes and deletes blobs, and reads everything back.
- **Old data directory, new server.** The previous server writes a directory and stops. `ombra_verify` of the new version reports it clean, the new server opens it, the new client reads and writes, and the recovery counts in the log are plausible.
- **Every frozen data directory.** Each release freezes a small one under `tests/fixtures/datadir-<version>/` (or as a release asset if too large for git). The new server must open every one.

For a tagged release, any failure fails the build. For an untagged commit given by `OMBRA_PREV_REF`, a failed pairing is an expected incompatibility, so a deliberate protocol change before the first release does not fail the build.

Promised, and only this: a client and server one release apart work together for the calls above, and a data directory written by a release opens in the next one. Two releases apart and GPU search across versions are not tested.

## Existing collections

A collection keeps its preset, dimension and key tag for life. A new version reads it as long as the formats are at versions it still reads, listed on [File formats](../reference/file-formats/index.md). The client key file has a version too (currently 1); an unknown version is refused.

If a release changes a ciphertext layout or a preset, older collections cannot be read in place: create a new collection and insert your vectors again. The server cannot convert encrypted data. No release so far has needed this. [`ombra-migrate`](migration.md) does it: it builds a new collection from the stored vectors (or re-embeds the documents with a new model), switches an alias and drops the old one after a drain period. It also changes the preset or keys.

Metadata converts in place when the server opens it, with the old file set aside: the catalog (schema 1 and 2 to 3) and the API key file (`api_keys.v1` to `api_keys.v2` when the first collection-limited key is made). A future change to an immutable server file (segment, blob) will come with an offline `ombra_convert --data-dir` tool for a stopped server and a fresh backup. A key file will get `ombra convert-keyfile`.
