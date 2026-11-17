# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""`FakeClient`: an in-memory test double with the API of `ombra.Client`, for unit tests of code
that uses Ombra.

It is NOT a mode of Ombra and not a way to run it: nothing is encrypted, nothing is persisted,
there is no network and no key file is written. Ranking is an exact cosine search over the vectors
you give it, so a test of your own code can run in milliseconds without OpenFHE. Use `Client.local`
to try Ombra itself.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ombra._core import AliasInfo, CollectionInfo, SearchHit, StoredRecord
from ombra.errors import AliasConflictError, OmbraError
from ombra.retry import RetryPolicy


@dataclass
class _Coll:
    preset: str
    security_level: str
    field_names: tuple[str, ...]
    cards: tuple[int, ...]
    ids: list[str] = field(default_factory=list)
    vectors: list[np.ndarray] = field(default_factory=list)
    metadata: list[dict[str, Any]] = field(default_factory=list)
    documents: list[str | bytes | None] = field(default_factory=list)
    deleted: int = 0


class FakeClient:
    """Same public methods and signatures as `ombra.Client`; keeps everything in memory."""

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
        self._colls: dict[str, _Coll] = {}
        self._aliases: dict[str, AliasInfo] = {}
        self._info: dict[str, dict[str, Any]] = {}

    @classmethod
    def local(cls, path: str | Path) -> FakeClient:
        """Return a `FakeClient`; `path` is ignored."""
        return cls("memory")

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
        scrypt_params: tuple[int, int, int] | None = None,
    ) -> str:
        """Create an in-memory collection and insert the vectors.

        Vectors are normalized to unit length. `key_file`, `passphrase`, `n_clusters`,
        `range_fields` and `scrypt_params` are accepted and ignored; no key file is written.

        Returns:
            The collection name.

        Raises:
            ValueError: The collection exists, or ids are missing or not unique.
        """
        if name in self._colls:
            raise ValueError(f"collection {name!r} exists")
        self._colls[name] = _Coll(
            preset or "fast",
            security_level,
            tuple(filter_fields or {}),
            tuple((filter_fields or {}).values()),
        )
        self.insert(name, vectors, ids, metadata, documents)
        return name

    def open_collection(
        self,
        name: str,
        *,
        key_file: str | Path,
        passphrase: str,
        filter_fields: Sequence[str] | None = None,
    ) -> str:
        """Resolve an alias and check the collection exists; the key file is not read.

        Returns:
            The real collection name.

        Raises:
            KeyError: There is no such collection.
        """
        real = self._aliases[name].target if name in self._aliases else name
        self._get(real)
        return real

    def get_collection(self, name: str) -> CollectionInfo:
        """Return a `CollectionInfo`; `cluster_count` is always 1."""
        name = self._aliases[name].target if name in self._aliases else name
        c = self._get(name)
        dim = len(c.vectors[0]) if c.vectors else 0
        return CollectionInfo(
            name=name,
            preset=c.preset,
            dimension=dim,
            security_level=c.security_level,
            filter_cardinalities=c.cards,
            cluster_count=1,
            vector_count=len(c.ids),
            deleted_count=c.deleted,
        )

    def list_collections(self) -> list[str]:
        """Return the collection names, sorted."""
        return sorted(self._colls)

    def drop_collection(self, name: str) -> None:
        """Delete a collection.

        Raises:
            KeyError: There is no such collection.
            OmbraError: An alias points at it.
        """
        self._get(name)
        if any(a.target == name for a in self._aliases.values()):
            raise OmbraError("an alias points at this collection")
        del self._colls[name]
        self._info.pop(name, None)

    def create_alias(self, name: str, target: str) -> AliasInfo:
        """Make `name` an alias of `target`.

        Raises:
            OmbraError: The name is a collection or an alias, or ends in `.g<number>`.
            KeyError: `target` does not exist.
        """
        if name in self._colls or name in self._aliases or re.search(r"\.g\d+$", name):
            raise OmbraError("the alias name is taken or not allowed")
        self._get(target)
        self._aliases[name] = AliasInfo(name, target, "", int(time.time()))
        return self._aliases[name]

    def switch_alias(self, name: str, new_target: str, expected_target: str) -> AliasInfo:
        """Move an alias to `new_target` if it points at `expected_target`.

        Raises:
            AliasConflictError: The alias points elsewhere.
            OmbraError: There is no such alias.
        """
        a = self._aliases.get(name)
        if a is None:
            raise OmbraError("alias not found")
        if a.target == new_target and expected_target in (new_target, a.previous):
            return a  # a repeat of a switch that was applied
        if a.target != expected_target:
            raise AliasConflictError("the alias does not point at the expected collection")
        self._get(new_target)
        self._aliases[name] = AliasInfo(name, new_target, a.target, int(time.time()))
        return self._aliases[name]

    def drop_alias(self, name: str) -> None:
        """Delete an alias.

        Raises:
            OmbraError: There is no such alias.
        """
        if self._aliases.pop(name, None) is None:
            raise OmbraError("alias not found")

    def list_aliases(self) -> list[AliasInfo]:
        """Return the aliases, sorted by name."""
        return [self._aliases[n] for n in sorted(self._aliases)]

    def iter_records(self, collection: str, *, ids_only: bool = False) -> Iterator[StoredRecord]:
        """Yield a `StoredRecord` per vector, with the vector as float32."""
        c = self._get(collection)
        for i, ident in enumerate(list(c.ids)):
            if ids_only:
                yield StoredRecord(ident)
            else:
                yield StoredRecord(
                    ident, c.vectors[i].astype("<f4"), dict(c.metadata[i]), c.documents[i]
                )

    def get_collection_info(self, collection: str) -> dict[str, Any] | None:
        """Return the stored info dict, or None."""
        self._get(collection)
        return self._info.get(collection)

    def set_collection_info(self, collection: str, info: dict[str, Any]) -> None:
        """Store an info dict for the collection; no size limit applies."""
        self._get(collection)
        self._info[collection] = dict(info)

    def insert(
        self,
        collection: str,
        vectors: Sequence[Sequence[float]],
        ids: Sequence[str] | None = None,
        metadata: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str | bytes | None] | None = None,
    ) -> list[str]:
        """Add vectors, normalized to unit length.

        Returns:
            The new ids. Without `ids` they are `fake-<n>`.

        Raises:
            KeyError: There is no such collection.
            ValueError: Ids are missing, not unique or already used, or metadata or documents
                do not have one entry per vector.
        """
        c = self._get(collection)
        x = np.asarray(vectors, dtype=np.float64)
        n = len(x)
        new = list(ids) if ids is not None else [f"fake-{len(c.ids) + i}" for i in range(n)]
        if len(new) != n or len(set(new)) != n or set(new) & set(c.ids):
            raise ValueError("one unique new id per vector")
        for seq in (metadata, documents):
            if seq is not None and len(seq) != n:
                raise ValueError("one metadata and one document entry per vector")
        for i, ident in enumerate(new):
            c.ids.append(ident)
            c.vectors.append(x[i] / np.linalg.norm(x[i]))
            c.metadata.append(dict(metadata[i]) if metadata is not None else {})
            c.documents.append(documents[i] if documents is not None else None)
        return new

    def delete(self, collection: str, ids: Sequence[str]) -> None:
        """Delete vectors by id; unknown ids are ignored."""
        c = self._get(collection)
        for ident in ids:
            if ident in c.ids:
                i = c.ids.index(ident)
                for lst in (c.ids, c.vectors, c.metadata, c.documents):
                    del lst[i]
                c.deleted += 1

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
    ) -> list[SearchHit]:
        """Exact cosine search over the vectors, best first.

        `filter` takes a value, a list of values or a range dict such as `{"gte": 1}` per
        metadata field. `level` and `n_probe` are ignored; `rerank=False` leaves the metadata
        out, as the real client does.
        """
        c = self._get(collection)
        q = np.asarray(query, dtype=np.float64)
        q = q / np.linalg.norm(q)
        rows = []
        for i, vec in enumerate(c.vectors):
            if all(self._match(c.metadata[i].get(f), want) for f, want in (filter or {}).items()):
                rows.append((float(vec @ q), i))
        rows.sort(key=lambda r: (-r[0], r[1]))
        return [SearchHit(c.ids[i], s, c.metadata[i] if rerank else {}) for s, i in rows[:k]]

    def close(self) -> None:
        """Forget every collection."""
        self._colls.clear()

    def _get(self, name: str) -> _Coll:
        try:
            return self._colls[name]
        except KeyError:
            raise KeyError(f"collection {name!r} does not exist") from None

    @staticmethod
    def _match(value: Any, want: Any) -> bool:
        if isinstance(want, list | tuple | set):
            return value in want
        if isinstance(want, Mapping):  # a range: gte, gt, lte, lt
            from ombra.ranges import parse_bounds

            lo, hi = parse_bounds(want)
            return isinstance(value, int | np.integer) and lo <= int(value) <= hi
        return bool(value == want)
