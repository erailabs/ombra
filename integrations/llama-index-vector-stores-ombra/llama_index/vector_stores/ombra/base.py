# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""`OmbraVectorStore`: a LlamaIndex vector store over an Ombra collection.

Vectors, metadata and the text of each node are encrypted on this machine before they leave it;
the server (or the in-process local mode) only computes on ciphertexts. The secret key lives in
the key file you name, protected by your passphrase. Back that file up: without it the data cannot
be read.

Scores. `query` returns the exact cosine similarity between the query and the stored vector, in
[-1, 1], as `similarities`. HIGHER IS BETTER. Results come best first.

Filters. Ombra filters on integer fields whose number of values is fixed when the collection is
created. Pass `filter_fields={"topic": 5}`, put an integer in [0, 5) under "topic" in every node's
metadata, and query with `MetadataFilters(filters=[MetadataFilter(key="topic", value=2)])`. The
operators `EQ` (one integer) and `IN` (a list of integers) work, combined with `AND` (the
default). Anything else raises `ValueError`: `OR`, `NOT`, nested filters, other operators, a field
that was not declared, a value that is not an integer, the same field twice. This class never
filters results on the client behind your back.

Deleting. Ombra deletes by id and cannot list the ids of a document, so the store keeps a map from
each `ref_doc_id` to its node ids. See `manifest_path`.

Not supported: query modes other than DEFAULT, `node_ids` or `doc_ids` in a query, `get_nodes`,
`clear`, and `delete_nodes` with filters.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from llama_index.core.schema import BaseNode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
    VectorStoreQueryMode,
    VectorStoreQueryResult,
)
from llama_index.core.vector_stores.utils import metadata_dict_to_node, node_to_metadata_dict
from pydantic import PrivateAttr

_MANIFEST_VERSION = 1


def _ref_key(ref_doc_id: str) -> str:
    return hashlib.sha256(ref_doc_id.encode("utf-8")).hexdigest()


class OmbraVectorStore(BasePydanticVectorStore):
    """A LlamaIndex vector store backed by one Ombra collection.

    The collection is created on the first `add` (Ombra needs data to cluster); until then queries
    return nothing. If it already exists on the server it is reopened with `key_file` and
    `passphrase`. A key file that exists while the collection does not is refused, so that a key is
    never overwritten.

    `manifest_path` (optional) is a JSON file that keeps the map from `ref_doc_id` to node ids
    between runs, so `delete(ref_doc_id)` also works after the store is reopened. It holds the
    SHA-256 of each ref_doc_id and the node ids, which the server sees anyway, and no text,
    metadata or vector. Without it the map lives in memory only: a reopened store refuses to
    delete a `ref_doc_id` it did not add itself (`delete_nodes` by node id always works).
    """

    stores_text: bool = True
    flat_metadata: bool = False

    _client: Any = PrivateAttr()
    _name: str = PrivateAttr()
    _key_file: Path = PrivateAttr()
    _passphrase: str | None = PrivateAttr()
    _filter_fields: dict[str, int] = PrivateAttr()
    _preset: str | None = PrivateAttr()
    _security_level: str = PrivateAttr()
    _scrypt_params: tuple[int, int, int] | None = PrivateAttr()
    _manifest_path: Path | None = PrivateAttr()
    _refs: dict[str, list[str]] = PrivateAttr()
    _refs_complete: bool = PrivateAttr()
    _exists: bool = PrivateAttr()

    def __init__(
        self,
        client: Any = None,
        collection_name: str = "ombra",
        *,
        local_path: str | Path | None = None,
        key_file: str | Path,
        passphrase: str,
        filter_fields: Mapping[str, int] | None = None,
        preset: str | None = None,
        security_level: str = "fast",
        scrypt_params: tuple[int, int, int] | None = None,
        manifest_path: str | Path | None = None,
    ) -> None:
        """Give `client` (an `ombra.Client` for a server), or `local_path` for local mode."""
        if client is None:
            if local_path is None:
                raise ValueError("give client=... or local_path=... (local mode)")
            from ombra import Client

            client = Client.local(local_path)
        super().__init__()
        self._client = client
        self._name = collection_name
        self._key_file = Path(key_file)
        # Held only until the key file has been read or written, then dropped. Never part of
        # repr or of any error message.
        self._passphrase = passphrase
        self._filter_fields = dict(filter_fields or {})
        self._preset = preset
        self._security_level = security_level
        self._scrypt_params = scrypt_params
        self._manifest_path = Path(manifest_path) if manifest_path is not None else None
        self._refs = {}
        self._refs_complete = False
        self._exists = False
        if self._manifest_path is not None and self._manifest_path.exists():
            self._refs = self._read_manifest(self._manifest_path)
            self._refs_complete = True
        if collection_name in client.list_collections():
            client.open_collection(
                collection_name,
                key_file=self._key_file,
                passphrase=self._passphrase,
                filter_fields=list(self._filter_fields) or None,
            )
            self._exists = True
            self._passphrase = None

    @classmethod
    def class_name(cls) -> str:
        return "OmbraVectorStore"

    def __repr__(self) -> str:
        return f"OmbraVectorStore(collection={self._name!r})"

    @property
    def client(self) -> Any:
        return self._client

    def close(self) -> None:
        """Close the Ombra client (and the local-mode server process, if any)."""
        self._client.close()

    # ---- manifest -----------------------------------------------------------------------
    @staticmethod
    def _read_manifest(path: Path) -> dict[str, list[str]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _MANIFEST_VERSION:
            raise ValueError(f"{path}: unknown manifest version {data.get('version')!r}")
        return {str(k): [str(i) for i in v] for k, v in data["refs"].items()}

    def _write_manifest(self) -> None:
        if self._manifest_path is None:
            return
        body = json.dumps({"version": _MANIFEST_VERSION, "refs": self._refs}, sort_keys=True)
        folder = self._manifest_path.parent
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".manifest-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, self._manifest_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ---- writing ------------------------------------------------------------------------
    @staticmethod
    def _unit(vectors: Sequence[Sequence[float]]) -> np.ndarray:
        x = np.asarray(vectors, dtype=np.float64)
        if x.ndim != 2:
            raise ValueError("embeddings must be a list of equal-length vectors")
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        if not np.all(norms > 0):
            raise ValueError("an embedding has zero length and cannot be normalised")
        return x / norms

    def add(self, nodes: Sequence[BaseNode], **add_kwargs: Any) -> list[str]:
        """Encrypt and store `nodes` (they must carry embeddings); return their node ids.

        A node id that is already stored is refused by the server ("duplicate id") and nothing is
        overwritten. Ids cannot be replaced in place: Ombra keeps a deleted id reserved until
        compaction, so to change a node, delete it and add the new version under a new id.
        """
        if not nodes:
            return []
        ids = [n.node_id for n in nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")
        vectors = self._unit([n.get_embedding() for n in nodes])
        metas = [
            node_to_metadata_dict(n, remove_text=False, flat_metadata=self.flat_metadata)
            for n in nodes
        ]
        for n in nodes:
            for name in self._filter_fields:
                if name not in n.metadata:
                    raise ValueError(f"node {n.node_id!r} has no metadata field {name!r}")

        if not self._exists:
            if self._key_file.exists():
                raise FileExistsError(
                    "the key file exists but the collection does not; refusing to overwrite a "
                    "key. Use another key_file, or restore the collection."
                )
            if self._passphrase is None:
                raise RuntimeError("the store has no passphrase left to create the collection")
            try:
                self._client.create_collection(
                    self._name,
                    vectors,
                    ids,
                    key_file=self._key_file,
                    passphrase=self._passphrase,
                    preset=self._preset,
                    metadata=metas,
                    filter_fields=self._filter_fields or None,
                    security_level=self._security_level,
                    scrypt_params=self._scrypt_params,
                )
            except BaseException:
                # The key file was written before the server saw anything and the collection was
                # not created: a leftover file would block the retry. It did not exist before.
                self._key_file.unlink(missing_ok=True)
                raise
            self._exists = True
            self._passphrase = None
            if self._manifest_path is None or not self._manifest_path.exists():
                self._refs_complete = True  # the collection is new: we know every node in it
        else:
            self._client.insert(self._name, vectors, ids, metas)
        for n in nodes:
            ref = _ref_key(n.ref_doc_id or n.node_id)
            self._refs.setdefault(ref, []).append(n.node_id)
        self._write_manifest()
        return ids

    def delete(self, ref_doc_id: str, **delete_kwargs: Any) -> None:
        """Delete every node added with this `ref_doc_id` (or the node with this id)."""
        key = _ref_key(ref_doc_id)
        if key not in self._refs:
            if not self._refs_complete:
                raise RuntimeError(
                    "this store does not know the nodes of that ref_doc_id: it was reopened "
                    "without a manifest. Give manifest_path=... when you add documents, or use "
                    "delete_nodes with node ids."
                )
            return  # unknown ref_doc_id: nothing to delete
        if self._exists:
            self._client.delete(self._name, list(self._refs[key]))
        del self._refs[key]
        self._write_manifest()

    def delete_nodes(
        self,
        node_ids: list[str] | None = None,
        filters: MetadataFilters | None = None,
        **delete_kwargs: Any,
    ) -> None:
        """Delete nodes by id. Unknown ids are ignored. Filters are not supported."""
        if filters is not None:
            raise ValueError("delete_nodes with filters is not supported; give node_ids")
        if not node_ids:
            raise ValueError("delete_nodes needs node_ids")
        if self._exists:
            self._client.delete(self._name, list(node_ids))
        gone = set(node_ids)
        self._refs = {
            k: kept for k, v in self._refs.items() if (kept := [i for i in v if i not in gone])
        }
        self._write_manifest()

    # ---- reading ------------------------------------------------------------------------
    def _check_filters(self, filters: MetadataFilters | None) -> dict[str, Any] | None:
        if filters is None or not filters.filters:
            return None
        condition = filters.condition or FilterCondition.AND
        if condition != FilterCondition.AND:
            raise ValueError(
                f"filter condition {condition!r} is not supported: Ombra filters combine fields "
                "with AND only"
            )
        out: dict[str, Any] = {}
        for f in filters.filters:
            if not isinstance(f, MetadataFilter):
                raise ValueError("nested filters are not supported")
            if f.key not in self._filter_fields:
                raise ValueError(
                    f"cannot filter on {f.key!r}: Ombra filters only on fields declared with "
                    f"filter_fields (declared: {sorted(self._filter_fields)})"
                )
            if f.key in out:
                raise ValueError(f"field {f.key!r} appears twice; use one IN filter instead")
            if f.operator == FilterOperator.EQ:
                if not self._is_int(f.value):
                    raise ValueError(f"filter on {f.key!r} must be an integer")
                out[f.key] = int(f.value)
            elif f.operator == FilterOperator.IN:
                if not isinstance(f.value, list | tuple | set) or not f.value:
                    raise ValueError(f"IN filter on {f.key!r} needs a non-empty list of integers")
                if not all(self._is_int(v) for v in f.value):
                    raise ValueError(f"IN filter on {f.key!r} must hold integers only")
                out[f.key] = sorted({int(v) for v in f.value})
            else:
                raise ValueError(
                    f"filter operator {f.operator!r} is not supported: Ombra filters support EQ "
                    "and IN on integer fields"
                )
        return out

    @staticmethod
    def _is_int(v: Any) -> bool:
        return isinstance(v, int | np.integer) and not isinstance(v, bool)

    def query(self, query: VectorStoreQuery, **kwargs: Any) -> VectorStoreQueryResult:
        """The `similarity_top_k` nearest nodes; `similarities` are cosines, higher is better."""
        if query.mode != VectorStoreQueryMode.DEFAULT:
            raise ValueError(f"query mode {query.mode!r} is not supported; only DEFAULT")
        if query.node_ids or query.doc_ids:
            raise ValueError("node_ids and doc_ids in a query are not supported")
        if query.query_embedding is None:
            raise ValueError("the query needs query_embedding")
        flt = self._check_filters(query.filters)
        if not self._exists or query.similarity_top_k <= 0:
            return VectorStoreQueryResult(nodes=[], similarities=[], ids=[])
        q = self._unit([query.query_embedding])[0]
        hits = self._client.search(self._name, q, query.similarity_top_k, flt, **kwargs)
        nodes, sims, ids = [], [], []
        for h in hits:
            nodes.append(metadata_dict_to_node(dict(h.metadata)))
            sims.append(float(h.score))
            ids.append(h.id)
        return VectorStoreQueryResult(nodes=nodes, similarities=sims, ids=ids)
