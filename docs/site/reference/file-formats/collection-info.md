# Collection info

An encrypted record of the embedding model a collection was built for (see [Migrating a collection](../../operations/migration.md)). Magic `OINF`, version 1. It is a record blob (kind 1) under the reserved id `\x00ombra/info`, so the server sees only a blob of the smallest size. See [Blobs](blobs.md).

## Layout

The plaintext, read only by the client:

Sizes are in bytes.

- 4: magic `OINF`
- 2: version, 1
- 4: length of the JSON object
- length: UTF-8 JSON object, at most 4096 bytes
- rest: zero padding to a multiple of 4

## Compatibility

A reader refuses another magic or version. [`ombra-migrate`](../cli/ombra-migrate.md) writes the keys `model`, `source`, `migration`, `created` and `preset`; other keys are kept as they are.
