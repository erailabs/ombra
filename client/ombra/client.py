# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Synchronous and asynchronous Ombra clients with identical public methods.

    client = Client("host:443", api_key="...")   # a GPU server (pip install ombra[grpc])
    client = Client.local("./ombra-data")         # in-process CPU server (pip install ombra[local])

Both modes run the same client code (keys, encryption, clustering, top-k and re-ranking happen in
this process); only the transport differs. The secret key stays in the key file you name in
`create_collection` and is never sent anywhere. The async client has the same methods as
coroutines; the work itself is synchronous (it runs in the calling thread).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ombra._core import AliasInfo, ClientCore, CollectionInfo, SearchHit, StoredRecord
from ombra.retry import RetryPolicy


def _grpc_transport(
    address: str,
    api_key: str | None,
    root_certificates: bytes | None,
    insecure: bool,
    timeout: float | None = None,
    stream_timeout: float | None = None,
    deadlines: Mapping[str, float] | None = None,
    retry: RetryPolicy | None = None,
) -> Any:
    from ombra import _transport

    return _transport.GrpcTransport(
        address,
        api_key,
        root_certificates,
        insecure,
        timeout=_transport.DEFAULT_TIMEOUT if timeout is None else timeout,
        stream_timeout=(
            _transport.DEFAULT_STREAM_TIMEOUT if stream_timeout is None else stream_timeout
        ),
        deadlines=deadlines,
        retry=retry,
    )


def _local_transport(path: str | Path) -> Any:
    from ombra._local import LocalTransport

    return LocalTransport(path)


class Client:
    """Synchronous client."""

    def __init__(
        self,
        address: str,
        *,
        api_key: str | None = None,
        root_certificates: bytes | None = None,
        insecure: bool = False,
        timeout: float | None = None,
        stream_timeout: float | None = None,
        deadlines: Mapping[str, float] | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        """Connect to a server.

        TLS is used unless `insecure=True`. Every deadline bounds the whole call.

        Args:
            address: Server address as `host:port`.
            api_key: API key sent with every call, if the server needs one.
            root_certificates: PEM bytes of the CA that signed the server certificate. By
                default the system roots are used.
            insecure: No TLS. For loopback test servers only; a warning is raised for any
                other host.
            timeout: Deadline in seconds of a single-answer call that changes data
                (default 120 s).
            stream_timeout: Deadline in seconds of a call that streams data or searches
                (default 1800 s). Small reads get 30 s and one blob 300 s.
            deadlines: Deadlines in seconds for single calls by gRPC name, for example
                `{"Search": 600}`.
            retry: An `ombra.RetryPolicy` for the calls that are safe to repeat (default: 4
                attempts, backoff with full jitter, 120 s budget). `RetryPolicy.never()`
                turns retries off.
        """
        self._core = ClientCore(
            _grpc_transport(
                address,
                api_key,
                root_certificates,
                insecure,
                timeout,
                stream_timeout,
                deadlines,
                retry,
            )
        )

    @classmethod
    def local(cls, path: str | Path) -> Client:
        """Start a CPU (OpenFHE) server in a child process that keeps its files under `path`.

        Args:
            path: Directory where the local server keeps its files.

        Returns:
            A client that talks to that local server.
        """
        self = cls.__new__(cls)
        self._core = ClientCore(_local_transport(path))
        return self

    def create_collection(
        self,
        name: str,
        vectors: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        *,
        key_file: str | Path,
        passphrase: str,
        preset: str | None = None,
        metadata: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str | bytes | None] | None = None,
        filter_fields: Mapping[str, int] | None = None,
        range_fields: Mapping[str, Any] | None = None,
        security_level: str = "fast",
        n_clusters: int | None = None,
        decoy_group_size: int = 1,
        decoy_grouping: str = "neighbours",
        private_retrieval: bool = False,
        metadata_bytes: int | None = None,
        scrypt_params: tuple[int, int, int] | None = None,
    ) -> str:
        """Cluster and encrypt `vectors` (unit length), write the key file, create the collection.

        The collection stays open in this client. Back up the key file: without it the data is
        lost.

        Args:
            name: Collection name.
            vectors: One unit-length vector per row; at least one.
            ids: One unique id per vector (1 to 256 bytes each). Random ids if omitted.
            key_file: Path the encrypted key file is written to.
            passphrase: Passphrase that protects the key file.
            preset: Parameter preset. Defaults to "fast" in local mode and to "balanced"
                against a server.
            metadata: One dict per vector.
            documents: One document per vector, or None for a vector without one.
            filter_fields: Maps a field name to its number of values. Every metadata dict then
                needs an integer in range under that name.
            range_fields: Maps a field name to the cut points of its buckets (a list of up to 63
                ascending integers) or to `{"kind": "two_digit"}` (domain 0..4095). Every
                metadata dict then needs the raw integer under that name, and a filter can say
                `{"year": {"gte": 2020, "lt": 2024}}`.
            security_level: Default search level of the collection: "fast", "decoy" or "strict".
            n_clusters: Number of clusters. If omitted, the number of vectors divided by the
                preset's batch size, rounded, at least 1.
            decoy_group_size: Size g of fixed decoy groups for Decoy searches; 1 (the default) is
                off and keeps per-query random decoys. With g > 1 the cluster count is rounded
                down to a multiple of g, and a Decoy search asks for whole groups.
            decoy_grouping: "neighbours" (the default: clusters with close centers share a group,
                which costs the least recall) or "random" (a keyed random partition). Only used
                when `decoy_group_size` > 1.
            private_retrieval: Store a sealed record row with every block and re-rank searches
                with one private retrieval call, so the server does not learn which records
                were candidates. Costs about 132 KB of download per bucket (1.5 rows asked
                per wanted row), a one-time upload of about 41 MB of public keys, and the
                hits carry their metadata from the row, with no extra call, and
                `search(fetch_documents=True)` can return their documents (see `metadata_bytes`).
            metadata_bytes: Private retrieval only. Size in bytes of the metadata slot of each
                record row (default 512; at most 19,421 - 4 * dimension - 8 * n_ints, rounded
                down to 64). A vector whose metadata, as compact JSON, does not fit is refused.
                Generates a second key pair, kept in the same key file. Default off.
            scrypt_params: Key derivation cost `(n, r, p)` of the key file. Library default if
                omitted.

        Returns:
            The collection name.

        Raises:
            ValueError: `vectors` is not a non-empty 2-D array, ids are missing or not unique,
                or a metadata dict lacks a filter field.
            OmbraError: The server refused the call.
        """
        return self._core.create_collection(
            name,
            vectors,
            ids,
            key_file=key_file,
            passphrase=passphrase,
            preset=preset,
            metadata=metadata,
            documents=documents,
            filter_fields=filter_fields,
            range_fields=range_fields,
            security_level=security_level,
            n_clusters=n_clusters,
            decoy_group_size=decoy_group_size,
            decoy_grouping=decoy_grouping,
            private_retrieval=private_retrieval,
            metadata_bytes=metadata_bytes,
            scrypt_params=scrypt_params,
        )

    def open_collection(
        self,
        name: str,
        *,
        key_file: str | Path,
        passphrase: str,
        filter_fields: Sequence[str] | None = None,
    ) -> str:
        """Reopen a collection with its key file; name the filter fields in their creation order.

        `name` may be an alias: the server resolves it once, the collection is pinned under its
        real name (the return value) and under the name you gave, and a later switch of the alias
        does not move it. Open the alias again to follow the switch.

        Args:
            name: Collection name or alias.
            key_file: Path of the key file written by `create_collection`.
            passphrase: Passphrase of the key file.
            filter_fields: Names of the equality filter fields in their creation order. The
                server stores only the value counts. Omit if the collection has none.

        Returns:
            The real collection name, which differs from `name` if `name` is an alias.

        Raises:
            ValueError: The number of names does not match the collection's equality fields.
            OmbraError: The server refused the call, for example `NotFoundError`.
        """
        return self._core.open_collection(
            name, key_file=key_file, passphrase=passphrase, filter_fields=filter_fields
        )

    def get_collection(self, name: str) -> CollectionInfo:
        """Read what the server stores about a collection.

        Args:
            name: Collection name.

        Returns:
            The collection's `CollectionInfo`.

        Raises:
            NotFoundError: There is no such collection.
        """
        return self._core.get_collection(name)

    def list_collections(self) -> list[str]:
        """List the collections on the server.

        Returns:
            The collection names, sorted.
        """
        return self._core.list_collections()

    def drop_collection(self, name: str) -> None:
        """Delete a collection from the server and close it in this client.

        Alias names pinned to the collection in this client are forgotten too. The call is
        never retried.

        Args:
            name: Collection name.

        Raises:
            NotFoundError: There is no such collection.
        """
        return self._core.drop_collection(name)

    def iter_records(self, collection: str, *, ids_only: bool = False) -> Iterator[StoredRecord]:
        """Read the live vectors of an open collection with their exact float32 vectors,
        metadata and documents, from the encrypted blobs, decrypted here.

        The server has no call that lists ids, so this reads every block of the collection; use
        it for migration and export, not in a request path.

        Args:
            collection: Name of an open collection.
            ids_only: Do not read the blobs; only the ids are set.

        Returns:
            An iterator of `StoredRecord`.
        """
        return self._core.iter_records(collection, ids_only=ids_only)

    def get_collection_info(self, collection: str) -> dict[str, Any] | None:
        """Read the encrypted "collection info" of an open collection.

        An example is the embedding model label that `ombra-migrate` wrote.

        Args:
            collection: Name of an open collection.

        Returns:
            The info dict, or None if there is none.
        """
        return self._core.get_collection_info(collection)

    def set_collection_info(self, collection: str, info: dict[str, Any]) -> None:
        """Store a small JSON object (up to 4 KiB) as the collection's encrypted info.

        Args:
            collection: Name of an open collection.
            info: JSON-serializable dict, up to 4 KiB.
        """
        self._core.set_collection_info(collection, info)

    def create_alias(self, name: str, target: str) -> AliasInfo:
        """Make `name` an alias of the collection `target` (admin key).

        The name may not be a collection or end in `.g<number>`.

        Args:
            name: New alias name.
            target: Collection the alias points at.

        Returns:
            The new `AliasInfo`.

        Raises:
            InvalidArgumentError: In local mode, which has no alias table.
            OmbraError: The server refused the call.
        """
        return self._core.create_alias(name, target)

    def switch_alias(self, name: str, new_target: str, expected_target: str) -> AliasInfo:
        """Move an alias to `new_target` if it points at `expected_target` now (admin key).

        Clients see the switch when they open the alias again; collections they have open keep
        working. Switching to the current target succeeds, so a retry after a lost answer is safe.

        Args:
            name: Alias name.
            new_target: Collection the alias should point at.
            expected_target: Collection the alias must point at now.

        Returns:
            The updated `AliasInfo`.

        Raises:
            AliasConflictError: The alias points elsewhere.
            InvalidArgumentError: In local mode, which has no alias table.
        """
        return self._core.switch_alias(name, new_target, expected_target)

    def drop_alias(self, name: str) -> None:
        """Delete an alias (admin key). The collection it pointed at is not touched.

        Args:
            name: Alias name.

        Raises:
            InvalidArgumentError: In local mode, which has no alias table.
            NotFoundError: There is no such alias.
        """
        return self._core.drop_alias(name)

    def list_aliases(self) -> list[AliasInfo]:
        """List the aliases on the server.

        Returns:
            One `AliasInfo` per alias.

        Raises:
            InvalidArgumentError: In local mode, which has no alias table.
        """
        return self._core.list_aliases()

    def insert(
        self,
        collection: str,
        vectors: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        metadata: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str | bytes | None] | None = None,
    ) -> list[str]:
        """Add vectors; their documents and metadata are stored encrypted.

        The call is never retried: a lost answer shows up as an error you handle yourself.

        Args:
            collection: Name of an open collection.
            vectors: One unit-length vector per row.
            ids: One unique id per vector (1 to 256 bytes each). Random ids if omitted.
            metadata: One dict per vector. Filter fields need their value here.
            documents: One document per vector, or None for a vector without one.

        Returns:
            The ids of the new vectors, in input order.

        Raises:
            KeyError: The collection is not open in this client.
            ValueError: Ids are missing, too long or not unique, or metadata lacks a filter field.
        """
        return self._core.insert(collection, vectors, ids, metadata, documents)

    def delete(self, collection: str, ids: Sequence[str]) -> None:
        """Delete vectors, and their record and document blobs, from an open collection.

        Args:
            collection: Name of an open collection.
            ids: Ids to delete, 1 to 256 bytes each.
        """
        return self._core.delete(collection, ids)

    def search(
        self,
        collection: str,
        query: Sequence[float],
        k: int = 10,
        filter: dict[str, Any] | None = None,
        *,
        level: str | None = None,
        n_probe: int | None = None,
        rerank: bool = True,
        fetch_documents: bool = False,
    ) -> list[SearchHit]:
        """Find the k nearest vectors (unit-length query) that pass `filter`, best first.

        Scores are exact cosine scores from the re-ranking records (approximate ones with
        `rerank=False`). If fewer than k vectors match
        after the search rounds, fewer hits are returned; the list is never padded.

        Args:
            collection: Name of an open collection.
            query: Unit-length query vector.
            k: Number of hits to return.
            filter: Maps a filter field name to one allowed value or a list of them (OR inside a
                field, AND across fields). A range field takes a dict such as
                `{"gte": 2020, "lt": 2024}`.
            level: Security level for this query: "fast", "decoy" or "strict". Defaults to the
                collection's level.
            n_probe: Cluster count of the first round. Default is the square root of the
                cluster count, rounded up.
            rerank: Fetch the record blobs of the best candidates and rank them by exact score
                (the default). With False, no blob is requested, so the server does not learn
                which records were candidates; scores are the decrypted approximate ones and
                metadata is empty.

        Returns:
            Up to k `SearchHit`, best first.

        With `fetch_documents=True` (private retrieval collections only) each hit also carries
        its document, fetched privately when it is 256 KiB or smaller; see
        `SearchHit.document_status`.

        Raises:
            KeyError: The collection is not open in this client.
            ValueError: A filter field is unknown, `level` is not a security level, or
                `rerank=False` with a range filter whose bounds are not on cut points.
        """
        return self._core.search(
            collection,
            query,
            k,
            filter,
            level=level,
            n_probe=n_probe,
            rerank=rerank,
            fetch_documents=fetch_documents,
        )

    def close(self) -> None:
        """Close every open collection, stop the decrypt workers and close the connection.

        The client cannot be used afterwards.
        """
        self._core.close()


class AsyncClient:
    """Asynchronous client."""

    def __init__(
        self,
        address: str,
        *,
        api_key: str | None = None,
        root_certificates: bytes | None = None,
        insecure: bool = False,
        timeout: float | None = None,
        stream_timeout: float | None = None,
        deadlines: Mapping[str, float] | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        """Connect to a server.

        TLS is used unless `insecure=True`. Every deadline bounds the whole call.

        Args:
            address: Server address as `host:port`.
            api_key: API key sent with every call, if the server needs one.
            root_certificates: PEM bytes of the CA that signed the server certificate. By
                default the system roots are used.
            insecure: No TLS. For loopback test servers only; a warning is raised for any
                other host.
            timeout: Deadline in seconds of a single-answer call that changes data
                (default 120 s).
            stream_timeout: Deadline in seconds of a call that streams data or searches
                (default 1800 s). Small reads get 30 s and one blob 300 s.
            deadlines: Deadlines in seconds for single calls by gRPC name, for example
                `{"Search": 600}`.
            retry: An `ombra.RetryPolicy` for the calls that are safe to repeat (default: 4
                attempts, backoff with full jitter, 120 s budget). `RetryPolicy.never()`
                turns retries off.
        """
        self._core = ClientCore(
            _grpc_transport(
                address,
                api_key,
                root_certificates,
                insecure,
                timeout,
                stream_timeout,
                deadlines,
                retry,
            )
        )

    @classmethod
    def local(cls, path: str | Path) -> AsyncClient:
        """Start a CPU (OpenFHE) server in a child process that keeps its files under `path`.

        Args:
            path: Directory where the local server keeps its files.

        Returns:
            A client that talks to that local server.
        """
        self = cls.__new__(cls)
        self._core = ClientCore(_local_transport(path))
        return self

    async def create_collection(
        self,
        name: str,
        vectors: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        *,
        key_file: str | Path,
        passphrase: str,
        preset: str | None = None,
        metadata: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str | bytes | None] | None = None,
        filter_fields: Mapping[str, int] | None = None,
        range_fields: Mapping[str, Any] | None = None,
        security_level: str = "fast",
        n_clusters: int | None = None,
        decoy_group_size: int = 1,
        decoy_grouping: str = "neighbours",
        private_retrieval: bool = False,
        metadata_bytes: int | None = None,
        scrypt_params: tuple[int, int, int] | None = None,
    ) -> str:
        """Cluster and encrypt `vectors` (unit length), write the key file, create the collection.

        The collection stays open in this client. Back up the key file: without it the data is
        lost.

        Args:
            name: Collection name.
            vectors: One unit-length vector per row; at least one.
            ids: One unique id per vector (1 to 256 bytes each). Random ids if omitted.
            key_file: Path the encrypted key file is written to.
            passphrase: Passphrase that protects the key file.
            preset: Parameter preset. Defaults to "fast" in local mode and to "balanced"
                against a server.
            metadata: One dict per vector.
            documents: One document per vector, or None for a vector without one.
            filter_fields: Maps a field name to its number of values. Every metadata dict then
                needs an integer in range under that name.
            range_fields: Maps a field name to the cut points of its buckets (a list of up to 63
                ascending integers) or to `{"kind": "two_digit"}` (domain 0..4095). Every
                metadata dict then needs the raw integer under that name, and a filter can say
                `{"year": {"gte": 2020, "lt": 2024}}`.
            security_level: Default search level of the collection: "fast", "decoy" or "strict".
            n_clusters: Number of clusters. If omitted, the number of vectors divided by the
                preset's batch size, rounded, at least 1.
            decoy_group_size: Size g of fixed decoy groups for Decoy searches; 1 (the default) is
                off and keeps per-query random decoys. With g > 1 the cluster count is rounded
                down to a multiple of g, and a Decoy search asks for whole groups.
            decoy_grouping: "neighbours" (the default: clusters with close centers share a group,
                which costs the least recall) or "random" (a keyed random partition). Only used
                when `decoy_group_size` > 1.
            private_retrieval: Store a sealed record row with every block and re-rank searches
                with one private retrieval call, so the server does not learn which records
                were candidates. Costs about 132 KB of download per bucket (1.5 rows asked
                per wanted row), a one-time upload of about 41 MB of public keys, and the
                hits carry their metadata from the row, with no extra call, and
                `search(fetch_documents=True)` can return their documents (see `metadata_bytes`).
            metadata_bytes: Private retrieval only. Size in bytes of the metadata slot of each
                record row (default 512; at most 19,421 - 4 * dimension - 8 * n_ints, rounded
                down to 64). A vector whose metadata, as compact JSON, does not fit is refused.
                Generates a second key pair, kept in the same key file. Default off.
            scrypt_params: Key derivation cost `(n, r, p)` of the key file. Library default if
                omitted.

        Returns:
            The collection name.

        Raises:
            ValueError: `vectors` is not a non-empty 2-D array, ids are missing or not unique,
                or a metadata dict lacks a filter field.
            OmbraError: The server refused the call.
        """
        return self._core.create_collection(
            name,
            vectors,
            ids,
            key_file=key_file,
            passphrase=passphrase,
            preset=preset,
            metadata=metadata,
            documents=documents,
            filter_fields=filter_fields,
            range_fields=range_fields,
            security_level=security_level,
            n_clusters=n_clusters,
            decoy_group_size=decoy_group_size,
            decoy_grouping=decoy_grouping,
            private_retrieval=private_retrieval,
            metadata_bytes=metadata_bytes,
            scrypt_params=scrypt_params,
        )

    async def open_collection(
        self,
        name: str,
        *,
        key_file: str | Path,
        passphrase: str,
        filter_fields: Sequence[str] | None = None,
    ) -> str:
        """Reopen a collection with its key file; name the filter fields in their creation order.

        `name` may be an alias: the server resolves it once, the collection is pinned under its
        real name (the return value) and under the name you gave, and a later switch of the alias
        does not move it. Open the alias again to follow the switch.

        Args:
            name: Collection name or alias.
            key_file: Path of the key file written by `create_collection`.
            passphrase: Passphrase of the key file.
            filter_fields: Names of the equality filter fields in their creation order. The
                server stores only the value counts. Omit if the collection has none.

        Returns:
            The real collection name, which differs from `name` if `name` is an alias.

        Raises:
            ValueError: The number of names does not match the collection's equality fields.
            OmbraError: The server refused the call, for example `NotFoundError`.
        """
        return self._core.open_collection(
            name, key_file=key_file, passphrase=passphrase, filter_fields=filter_fields
        )

    async def get_collection(self, name: str) -> CollectionInfo:
        """Read what the server stores about a collection (see `Client.get_collection`)."""
        return self._core.get_collection(name)

    async def list_collections(self) -> list[str]:
        """List the collection names, sorted (see `Client.list_collections`)."""
        return self._core.list_collections()

    async def drop_collection(self, name: str) -> None:
        """Delete a collection from the server (see `Client.drop_collection`)."""
        return self._core.drop_collection(name)

    async def iter_records(
        self, collection: str, *, ids_only: bool = False
    ) -> Iterator[StoredRecord]:
        """Read the live vectors of an open collection (see `Client.iter_records`).

        The work runs in the calling thread.

        Args:
            collection: Name of an open collection.
            ids_only: Do not read the blobs; only the ids are set.

        Returns:
            An iterator of `StoredRecord`.
        """
        return self._core.iter_records(collection, ids_only=ids_only)

    async def get_collection_info(self, collection: str) -> dict[str, Any] | None:
        """The encrypted collection info of an open collection, or None (see
        `Client.get_collection_info`)."""
        return self._core.get_collection_info(collection)

    async def set_collection_info(self, collection: str, info: dict[str, Any]) -> None:
        """Store a small JSON object as the collection's encrypted info (see
        `Client.set_collection_info`)."""
        self._core.set_collection_info(collection, info)

    async def create_alias(self, name: str, target: str) -> AliasInfo:
        """Make `name` an alias of the collection `target` (admin key).

        The name may not be a collection or end in `.g<number>`.

        Args:
            name: New alias name.
            target: Collection the alias points at.

        Returns:
            The new `AliasInfo`.

        Raises:
            InvalidArgumentError: In local mode, which has no alias table.
            OmbraError: The server refused the call.
        """
        return self._core.create_alias(name, target)

    async def switch_alias(self, name: str, new_target: str, expected_target: str) -> AliasInfo:
        """Move an alias to `new_target` if it points at `expected_target` now (admin key).

        Clients see the switch when they open the alias again; collections they have open keep
        working. Switching to the current target succeeds, so a retry after a lost answer is safe.

        Args:
            name: Alias name.
            new_target: Collection the alias should point at.
            expected_target: Collection the alias must point at now.

        Returns:
            The updated `AliasInfo`.

        Raises:
            AliasConflictError: The alias points elsewhere.
            InvalidArgumentError: In local mode, which has no alias table.
        """
        return self._core.switch_alias(name, new_target, expected_target)

    async def drop_alias(self, name: str) -> None:
        """Delete an alias (see `Client.drop_alias`)."""
        return self._core.drop_alias(name)

    async def list_aliases(self) -> list[AliasInfo]:
        """List the aliases on the server (see `Client.list_aliases`)."""
        return self._core.list_aliases()

    async def insert(
        self,
        collection: str,
        vectors: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        metadata: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str | bytes | None] | None = None,
    ) -> list[str]:
        """Add vectors; their documents and metadata are stored encrypted.

        The call is never retried: a lost answer shows up as an error you handle yourself.

        Args:
            collection: Name of an open collection.
            vectors: One unit-length vector per row.
            ids: One unique id per vector (1 to 256 bytes each). Random ids if omitted.
            metadata: One dict per vector. Filter fields need their value here.
            documents: One document per vector, or None for a vector without one.

        Returns:
            The ids of the new vectors, in input order.

        Raises:
            KeyError: The collection is not open in this client.
            ValueError: Ids are missing, too long or not unique, or metadata lacks a filter field.
        """
        return self._core.insert(collection, vectors, ids, metadata, documents)

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        """Delete vectors from an open collection (see `Client.delete`)."""
        return self._core.delete(collection, ids)

    async def search(
        self,
        collection: str,
        query: Sequence[float],
        k: int = 10,
        filter: dict[str, Any] | None = None,
        *,
        level: str | None = None,
        n_probe: int | None = None,
        rerank: bool = True,
        fetch_documents: bool = False,
    ) -> list[SearchHit]:
        """Find the k nearest vectors (unit-length query) that pass `filter`, best first.

        Scores are exact cosine scores from the re-ranking records (approximate ones with
        `rerank=False`). If fewer than k vectors match
        after the search rounds, fewer hits are returned; the list is never padded.

        Args:
            collection: Name of an open collection.
            query: Unit-length query vector.
            k: Number of hits to return.
            filter: Maps a filter field name to one allowed value or a list of them (OR inside a
                field, AND across fields). A range field takes a dict such as
                `{"gte": 2020, "lt": 2024}`.
            level: Security level for this query: "fast", "decoy" or "strict". Defaults to the
                collection's level.
            n_probe: Cluster count of the first round. Default is the square root of the
                cluster count, rounded up.
            rerank: Fetch the record blobs of the best candidates and rank them by exact score
                (the default). With False, no blob is requested, so the server does not learn
                which records were candidates; scores are the decrypted approximate ones and
                metadata is empty.

        Returns:
            Up to k `SearchHit`, best first.

        With `fetch_documents=True` (private retrieval collections only) each hit also carries
        its document, fetched privately when it is 256 KiB or smaller; see
        `SearchHit.document_status`.

        Raises:
            KeyError: The collection is not open in this client.
            ValueError: A filter field is unknown, `level` is not a security level, or
                `rerank=False` with a range filter whose bounds are not on cut points.
        """
        return self._core.search(
            collection,
            query,
            k,
            filter,
            level=level,
            n_probe=n_probe,
            rerank=rerank,
            fetch_documents=fetch_documents,
        )

    async def close(self) -> None:
        """Close every open collection and the connection (see `Client.close`)."""
        self._core.close()
