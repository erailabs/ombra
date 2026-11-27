# Errors, retries and deadlines

Applies to `Client("host:port")`. Local mode makes one attempt per call.

## Deadlines

The deadline bounds the whole call, and a whole stream for a search or upload. Defaults: 30 s for small reads (`GetCollection`, `ListCollections`, `ListBlocks`), 120 s for other single-answer calls, 300 s for one blob, 1800 s for `Insert`, `UploadKeys`, `ReplaceBlock`, `GetBlock` and `Search`. In the [`Client`](client.md) constructor, `timeout` replaces 120 s, `stream_timeout` replaces 1800 s, and `deadlines={"Search": 600}` sets single calls by gRPC name.

## Errors

Every failed server call raises a subclass of `ombra.OmbraError` with:

- `retryable`: `True` if repeating the call may work.
- `grpc_status`: the gRPC status name, such as `"UNAVAILABLE"`, or `None` when the server answered with an Ombra error code.
- `error_code`: the `ombra.v1` `ErrorCode` number, or `None` for a transport failure.

`ServerUnavailableError` (UNAVAILABLE), `DeadlineExceededError` and `ResourceExhaustedError` are retryable. Others are not, for example `NotFoundError`, `InvalidArgumentError`, `PermissionDeniedError`, `UnauthenticatedError`, `KeyTagMismatchError` and `PresetMismatchError`. `CollectionError` and `BlobStoreError` still exist; errors that name an Ombra error code are also instances of them. Messages hold the call name and codes, never ids, vectors, scores or ciphertext bytes.

## Retries

The client retries a failed call when its error is retryable and a repeat is safe: `GetCollection`, `ListCollections`, `ListBlocks`, `GetBlock`, `GetBlob`, `Search`, `Delete`, `PutBlob`, `DeleteBlobs` and `UploadKeys`. `CreateCollection` is repeated too; if the repeat finds the collection already there, it counts as success only when the stored settings are identical. `Insert`, `ReplaceBlock` and `DropCollection` are never retried, so a lost answer shows up as an error you handle yourself. Before each retry the client waits a random time between zero and `min(max_delay, base_delay * 2**(attempt - 1))`.

A search whose stream breaks opens a new stream and sends the same query and cluster choices again, in the same order, never new decoys (two decoy sets would show which clusters are real).

Set the policy with `Client(..., retry=RetryPolicy(...))`:

```python
from ombra import Client, RetryPolicy

client = Client("db.example.com:443", retry=RetryPolicy(max_attempts=6, total_budget=60))
client = Client("db.example.com:443", retry=RetryPolicy.never())  # no retries
```

The defaults are 4 attempts, a 0.25 s base delay, an 8 s cap and a 120 s total budget. No new attempt starts if it would pass the budget. Retries are logged at debug level on the `ombra.retry` logger with the call name, attempt number and status code only.

## ombra.errors

::: ombra.errors
    options:
      heading_level: 3
      show_root_heading: false
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

## ombra.retry

::: ombra.retry
    options:
      heading_level: 3
      show_root_heading: false
      show_source: false
      show_signature_annotations: true
      separate_signature: true
      docstring_section_style: table
      members_order: source
      merge_init_into_class: true
      show_if_no_docstring: true

