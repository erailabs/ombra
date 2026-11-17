# Python API

The `ombra` package is the client: it holds the keys, encrypts, clusters, ranks and re-ranks. Install it with `pip install ombra`. The reference pages are generated from the docstrings in `client/ombra/`, and the build reads the source without importing it, because the package imports numpy.

## Exports

The package exports these names: `Client`, `AsyncClient`, `CollectionInfo`, `AliasInfo`, `StoredRecord`, `SearchHit`, `OmbraError`, `RetryPolicy`, `API_VERSION` and `__version__`. `API_VERSION` is the gRPC package the client speaks, `ombra.v1`.

## Public and internal modules

The modules you may touch directly are `ombra.errors`, `ombra.retry`, `ombra.collection`, `ombra.ranges`, `ombra.migrate`, `ombra.collection_info`, `ombra.presets` and `ombra.testing`. Everything else under `ombra.` (names starting with an underscore, `search`, `topk`, `clustering` and so on) is internal and can change between versions.

## AsyncClient

`AsyncClient` has the same methods as `Client`, as coroutines. The work still runs in the calling thread, so awaiting a call blocks the event loop until it returns.

For how to choose between `Client("host:port")` and `Client.local(path)`, see [Local mode or a server](../../getting-started/local-or-server.md).

## Pages

| Page | Contents |
| --- | --- |
| [Client](client.md) | `Client` and `AsyncClient`: connect, create and open collections, insert, search, delete, aliases. |
| [Results](results.md) | `CollectionInfo`, `AliasInfo`, `StoredRecord` and `SearchHit`. |
| [Errors, retries and deadlines](errors-and-retries.md) | `ombra.errors`, `ombra.retry`, which calls are repeated and what the deadlines are. |
| [Collections and key files](collections.md) | `ombra.collection`, key files, and the helpers for range filters. |
| [Migration](migration.md) | `ombra.migrate` and `ombra.collection_info`, the Python side of `ombra-migrate`. |
| [Presets](presets.md) | `ombra.presets`, the preset parameters in code. |
| [Testing](testing.md) | `FakeClient`, an in-memory stand-in for unit tests. |

`ombra-keys` is a command line tool, described on [its own page](../cli/ombra-keys.md).
