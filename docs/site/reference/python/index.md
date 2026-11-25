# Python API

The `ombra` package is the client (`pip install ombra`): it holds the keys, encrypts, clusters, ranks and re-ranks. These pages are generated from the docstrings in `client/ombra/`.

## Exports

The package exports these names: `Client`, `AsyncClient`, `CollectionInfo`, `AliasInfo`, `StoredRecord`, `SearchHit`, `OmbraError`, `RetryPolicy`, `API_VERSION` and `__version__`. `API_VERSION` is the gRPC package the client speaks, `ombra.v1`.

## Public and internal modules

Public modules: `ombra.errors`, `ombra.retry`, `ombra.collection`, `ombra.ranges`, `ombra.migrate`, `ombra.collection_info`, `ombra.presets` and `ombra.testing`. Everything else under `ombra.` (underscore names, `search`, `topk`, `clustering` and so on) is internal and can change.

## AsyncClient

`AsyncClient` has the same methods as `Client`, as coroutines. The work runs in the calling thread, so awaiting a call blocks the event loop until it returns. To choose between `Client("host:port")` and `Client.local(path)`, see [Local mode or a server](../../getting-started/local-or-server.md).

## Pages

- [Client](client.md): `Client` and `AsyncClient`: connect, create and open collections, insert, search, delete, aliases.
- [Results](results.md): `CollectionInfo`, `AliasInfo`, `StoredRecord` and `SearchHit`.
- [Errors, retries and deadlines](errors-and-retries.md): `ombra.errors`, `ombra.retry`, which calls are repeated and what the deadlines are.
- [Collections and key files](collections.md): `ombra.collection`, key files, and the helpers for range filters.
- [Migration](migration.md): `ombra.migrate` and `ombra.collection_info`, the Python side of `ombra-migrate`.
- [Presets](presets.md): `ombra.presets`, the preset parameters in code.
- [Testing](testing.md): `FakeClient`, an in-memory stand-in for unit tests.


