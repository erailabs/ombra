# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Migrate a collection to a new one with no downtime (decision 0028): `ombra-migrate`.

Why a new collection. The server cannot transform vectors. A new embedding model changes the
dimension or the meaning of every vector, and a new preset or OpenFHE version changes the
ciphertext layout. Both need new keys and new ciphertexts, which only the client can make. So the
tool builds `<base>.m<n>` beside the source, switches an alias to it, and drops the source after a
drain period.

Two modes, chosen at `plan`:

* rebuild: same model, new preset (or just fresh keys and dense blocks). The vectors are read from
  the exact float32 copies in the source's encrypted record blobs.
* re-embed: a new model. `embed` is a callable `embed(texts: list[str]) -> array (n, d)`; the
  texts are the source documents, so every vector needs a document.

Steps, each resumable from the state file (`ombra-migrate.json`, no secrets):

    plan      choose the target name `<base>.m<n>`, write the state file
    start     make the target: new key file (saved before the server sees anything), cluster a
              sample, create the collection, write the collection info (model label)
    backfill  copy every live source vector that the target does not have yet, in batches. Your
              application keeps writing: send its inserts and deletes through
              `Migration.insert` / `Migration.delete` (dual write), or any write you make to the
              source after the backfill pass is missed.
    verify    gates: live counts equal; every target id has a record blob; self-retrieval on a
              sample; encrypted-search recall against exact plaintext search on the target's own
              vectors. The overlap with the source's answers is reported but is not a gate (a new
              model has different neighbours).
    switch    `SwitchAlias(alias, target, expected=source)`, one compare-and-swap on the server.
              Needs the verified state. Local mode has no aliases: the step records the switch
              and you point the application at the new name.
    finish    after the drain period (default 24 hours), drop the source. Its key file is kept.
    abort     drop the target, any time before `switch`.

Blobs are not copied byte for byte: their associated data binds the collection name, and the
target has its own name and its own document master key. Every record and document is opened with
the source's keys and sealed again with the target's, which is also what a key change needs
(decision 0031). The target therefore always gets a new FHE key pair and a new master key.
Nothing here prints or stores a vector, a document or a key.
"""

from __future__ import annotations

import argparse
import getpass
import importlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ombra.errors import AliasConflictError, OmbraError

STATE_FORMAT = "ombra-migrate"
STATE_VERSION = 1
DEFAULT_STATE = "ombra-migrate.json"
DEFAULT_DRAIN_HOURS = 24.0
PHASES = ("planned", "started", "backfilled", "verified", "switched", "finished", "aborted")
MODES = ("rebuild", "reembed")

Embedder = Callable[[list[str]], Any]


class MigrationError(OmbraError):
    """A migration step was refused (wrong phase, a gate failed, bad input). The message names the
    step and the reason; it never carries ids, vectors or documents."""


# ---- state file ------------------------------------------------------------------------------
@dataclass
class State:
    """The `ombra-migrate 1` state file. Plain JSON, no secrets (the key file is named, never
    its passphrase)."""

    source: str
    target: str
    mode: str
    phase: str = "planned"
    alias: str | None = None
    target_preset: str | None = None
    source_key_file: str = ""
    target_key_file: str = ""
    filter_fields: list[str] = field(default_factory=list)
    security_level: str = "fast"
    embedder: str | None = None
    model: str | None = None
    drain_hours: float = DEFAULT_DRAIN_HOURS
    n_clusters: int | None = None
    migration: int = 1
    created: float = 0.0
    backfilled: int = 0
    verify: dict[str, Any] | None = None
    switched_at: float | None = None
    switched_alias: bool = False
    finished_at: float | None = None

    def to_json(self) -> str:
        d = {"format": STATE_FORMAT, "version": STATE_VERSION, **asdict(self)}
        return json.dumps(d, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> State:
        try:
            d = json.loads(text)
        except ValueError:
            raise MigrationError("the state file is not valid JSON") from None
        if not isinstance(d, dict) or d.get("format") != STATE_FORMAT:
            raise MigrationError("not an ombra-migrate state file")
        if d.get("version") != STATE_VERSION:
            raise MigrationError(f"state file version {d.get('version')!r} is not supported")
        d = {k: v for k, v in d.items() if k not in ("format", "version")}
        try:
            st = cls(**d)
        except TypeError:
            raise MigrationError("the state file has unknown or missing fields") from None
        if st.phase not in PHASES or st.mode not in MODES:
            raise MigrationError("the state file has an unknown phase or mode")
        return st


def load_state(path: str | os.PathLike[str]) -> State:
    try:
        return State.from_json(Path(path).read_text())
    except FileNotFoundError:
        raise MigrationError(f"no state file at {path}; run plan first") from None


def save_state(path: str | os.PathLike[str], st: State) -> None:
    """Atomic: a crash leaves the old state or the new one, never half."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent or ".", prefix=p.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(st.to_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_embedder(spec: str) -> Embedder:
    """`pkg.mod:fn` -> the callable."""
    mod, _, fn = spec.partition(":")
    if not mod or not fn:
        raise MigrationError("an embedder is written pkg.mod:function")
    try:
        obj = getattr(importlib.import_module(mod), fn)
    except (ImportError, AttributeError):
        raise MigrationError(f"cannot import the embedder {spec!r}") from None
    if not callable(obj):
        raise MigrationError(f"the embedder {spec!r} is not callable")
    return obj  # type: ignore[no-any-return]


def _unit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def target_name_for(source: str, existing: Iterable[str]) -> tuple[str, int]:
    """`<base>.m<n>` with n one above the highest migration of the base that exists."""
    base = re.sub(r"\.m\d+$", "", source)
    n = 0
    for name in existing:
        m = re.fullmatch(re.escape(base) + r"\.m(\d+)", name)
        if m:
            n = max(n, int(m.group(1)))
    return f"{base}.m{n + 1}", n + 1


def _supports_aliases(client: Any) -> bool:
    core = getattr(client, "_core", None)
    return bool(getattr(getattr(core, "_t", None), "supports_aliases", True))


# ---- the migration ---------------------------------------------------------------------------
class Migration:
    """One migration of one collection. Build it with `Migration.plan(...)` (new) or
    `Migration.resume(...)` (from the state file), then call the steps in order.

    `client` is an `ombra.Client` (or a `FakeClient` in tests). The source collection is opened
    with `source_passphrase`; the target gets its own key file `state.target_key_file`, written
    with `target_passphrase`. A new key kit is a parameter of the target only: rotating the keys
    later (decision 0031) is a plan with `target_key_file` set to a fresh path.
    """

    def __init__(
        self,
        client: Any,
        state: State,
        *,
        state_path: str | os.PathLike[str] = DEFAULT_STATE,
        source_passphrase: str,
        target_passphrase: str,
        embed: Embedder | None = None,
        scrypt_params: tuple[int, int, int] | None = None,
        batch_size: int = 1024,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.state = state
        self.state_path = Path(state_path)
        self._source_pw, self._target_pw = source_passphrase, target_passphrase
        self.embed = embed
        self.scrypt_params = scrypt_params
        self.batch_size = batch_size
        self._now = now
        self._source_open = False
        self._target_open = False
        if state.mode == "reembed" and embed is None and state.embedder:
            self.embed = load_embedder(state.embedder)

    def __repr__(self) -> str:
        return f"Migration({self.state.source!r} -> {self.state.target!r}, {self.state.phase})"

    # ---- plan / resume --------------------------------------------------------------------
    @classmethod
    def plan(
        cls,
        client: Any,
        source: str,
        *,
        source_key_file: str | os.PathLike[str],
        target_key_file: str | os.PathLike[str],
        source_passphrase: str,
        target_passphrase: str,
        state_path: str | os.PathLike[str] = DEFAULT_STATE,
        filter_fields: Sequence[str] = (),
        alias: str | None = None,
        target_preset: str | None = None,
        embed: Embedder | None = None,
        embedder: str | None = None,
        model: str | None = None,
        drain_hours: float = DEFAULT_DRAIN_HOURS,
        n_clusters: int | None = None,
        **options: Any,
    ) -> Migration:
        """Choose the target and write the state file. `embed` (or `embedder` as `pkg.mod:fn`)
        selects re-embed mode, otherwise the mode is rebuild. Nothing is created on the server."""
        if Path(state_path).exists():
            raise MigrationError(f"{state_path} exists: resume it, or abort and remove it first")
        if Path(target_key_file).exists():
            raise MigrationError("the target key file exists; name a new path")
        if embed is None and embedder:
            embed = load_embedder(embedder)
        mode = "reembed" if embed is not None else "rebuild"
        if mode == "reembed" and not model:
            raise MigrationError("re-embed needs a model label, so the application can check it")
        info = client.get_collection(source)
        if alias is not None:
            if re.search(r"\.g\d+$", alias):
                raise MigrationError("an alias may not end in .g<number>")
            if alias == source:
                raise MigrationError("the alias must differ from the source collection name")
        existing = client.list_collections()
        if info.name != source:  # the source was given as an alias: migrate what it points at
            alias, source = alias or source, info.name
        target, n = target_name_for(source, existing)
        st = State(
            source=source,
            target=target,
            mode=mode,
            alias=alias,
            target_preset=target_preset or info.preset,
            source_key_file=str(source_key_file),
            target_key_file=str(target_key_file),
            filter_fields=list(filter_fields),
            security_level=info.security_level,
            embedder=embedder,
            model=model,
            drain_hours=drain_hours,
            n_clusters=n_clusters,
            migration=n,
            created=time.time(),
        )
        if len(st.filter_fields) != len(info.filter_cardinalities):
            raise MigrationError(
                f"the collection has {len(info.filter_cardinalities)} filter fields: name them "
                "all, in their creation order"
            )
        save_state(state_path, st)
        return cls(
            client,
            st,
            state_path=state_path,
            source_passphrase=source_passphrase,
            target_passphrase=target_passphrase,
            embed=embed,
            **options,
        )

    @classmethod
    def resume(
        cls,
        client: Any,
        *,
        state_path: str | os.PathLike[str] = DEFAULT_STATE,
        source_passphrase: str,
        target_passphrase: str,
        embed: Embedder | None = None,
        **options: Any,
    ) -> Migration:
        return cls(
            client,
            load_state(state_path),
            state_path=state_path,
            source_passphrase=source_passphrase,
            target_passphrase=target_passphrase,
            embed=embed,
            **options,
        )

    # ---- helpers --------------------------------------------------------------------------
    def _save(self) -> None:
        save_state(self.state_path, self.state)

    def _need(self, *phases: str) -> None:
        if self.state.phase not in phases:
            raise MigrationError(
                f"this step needs the phase {' or '.join(phases)}; the migration is at "
                f"{self.state.phase!r}"
            )

    def _open_source(self) -> None:
        if not self._source_open:
            s = self.state
            self.client.open_collection(
                s.source,
                key_file=s.source_key_file,
                passphrase=self._source_pw,
                filter_fields=s.filter_fields or None,
            )
            self._source_open = True

    def _open_target(self) -> None:
        if not self._target_open:
            s = self.state
            self.client.open_collection(
                s.target,
                key_file=s.target_key_file,
                passphrase=self._target_pw,
                filter_fields=s.filter_fields or None,
            )
            self._target_open = True

    def _vectors_for(self, recs: list[Any]) -> np.ndarray:
        """The vectors the target stores for these source records."""
        if self.state.mode == "rebuild":
            return _unit(np.stack([r.vector for r in recs]))
        assert self.embed is not None
        texts = []
        for r in recs:
            if not isinstance(r.document, str):
                raise MigrationError(
                    "re-embed needs a text document for every vector; "
                    f"{len(recs)} were read and at least one has none"
                )
            texts.append(r.document)
        out = _unit(np.asarray(self.embed(texts), dtype=np.float64))
        if out.ndim != 2 or len(out) != len(recs):
            raise MigrationError("the embedder must return one vector per text")
        return out

    def _usable(self, recs: Iterable[Any]) -> list[Any]:
        """Records whose blob is there. A vector without a record cannot be migrated."""
        out = list(recs)
        if any(r.vector is None for r in out):
            raise MigrationError(
                "a source vector has no record blob (it was not stored through this client); "
                "it cannot be migrated"
            )
        return out

    # ---- start ----------------------------------------------------------------------------
    def start(self, *, sample: int | None = None) -> None:
        """Create the target from a sample of the source (all of it if small). Resumable: a
        target that exists with the state's key file is reused."""
        s = self.state
        self._need("planned", "started")
        self._open_source()
        src = self.client.get_collection(s.source)
        if s.target in self.client.list_collections():  # an earlier run got this far
            self._open_target()
            if self.client.get_collection_info(s.target) is None:
                self.client.set_collection_info(s.target, self._info(src))
            s.phase = "started"
            self._save()
            return
        if Path(s.target_key_file).exists():
            raise MigrationError("the target key file exists but the target collection does not")
        live = src.vector_count - src.deleted_count
        if live < 1:
            raise MigrationError("the source has no live vectors")
        from ombra import presets

        slots = presets.derive(
            s.target_preset or src.preset, 384
        ).batch_size  # depends on the preset
        k = s.n_clusters or max(1, round(live / slots))
        want = min(live, max(20 * k, 1000)) if sample is None else min(live, sample)
        recs: list[Any] = []
        for r in self.client.iter_records(s.source):
            recs.append(r)
            if len(recs) >= want:
                break
        recs = self._usable(recs)
        vecs = self._vectors_for(recs)
        cards = src.filter_cardinalities
        self.client.create_collection(
            s.target,
            vecs,
            [r.id for r in recs],
            key_file=s.target_key_file,
            passphrase=self._target_pw,
            preset=s.target_preset,
            metadata=[r.metadata for r in recs]
            if (cards or any(r.metadata for r in recs))
            else None,
            documents=[r.document for r in recs],
            filter_fields=dict(zip(s.filter_fields, cards, strict=True)) or None,
            security_level=s.security_level,
            n_clusters=min(k, len(recs)),
            **({} if self.scrypt_params is None else {"scrypt_params": self.scrypt_params}),
        )
        self._target_open = True
        self.client.set_collection_info(s.target, self._info(src))
        s.phase = "started"
        self._save()

    def _info(self, src: Any) -> dict[str, Any]:
        """The collection info (v1) of the target: the model label and where it came from."""
        s = self.state
        info: dict[str, Any] = {
            "model": s.model or "",
            "source": s.source,
            "migration": s.migration,
            "created": int(self._now()),
            "preset": s.target_preset or src.preset,
        }
        if s.mode == "rebuild" and not s.model:
            old = self.client.get_collection_info(s.source)
            if old and old.get("model"):
                info["model"] = old["model"]  # same model: the label carries over
        return info

    # ---- backfill -------------------------------------------------------------------------
    def backfill(self, progress: Callable[[int], None] | None = None) -> int:
        """Copy the live source vectors that the target lacks. Safe to repeat and to resume.
        Returns the number copied by this call."""
        s = self.state
        self._need("started", "backfilled", "verified")
        self._open_source()
        self._open_target()
        have = {r.id for r in self.client.iter_records(s.target, ids_only=True)}
        copied = 0
        batch: list[Any] = []

        def flush() -> int:
            if not batch:
                return 0
            recs = self._usable(batch)
            vecs = self._vectors_for(recs)
            self.client.insert(
                s.target,
                vecs,
                [r.id for r in recs],
                [r.metadata for r in recs]
                if (s.filter_fields or any(r.metadata for r in recs))
                else None,
                [r.document for r in recs],
            )
            n = len(recs)
            batch.clear()
            s.backfilled += n
            self._save()
            if progress:
                progress(n)
            return n

        for r in self.client.iter_records(s.source):
            if r.id in have:
                continue
            batch.append(r)
            if len(batch) >= self.batch_size:
                copied += flush()
        copied += flush()
        s.phase = "backfilled"
        s.verify = None  # the target changed: verify again
        self._save()
        return copied

    # ---- dual write -----------------------------------------------------------------------
    def insert(
        self,
        vectors: Sequence[Sequence[float]],
        ids: Sequence[str],
        metadata: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str | bytes | None] | None = None,
        *,
        new_vectors: Sequence[Sequence[float]] | None = None,
    ) -> list[str]:
        """The application's insert during a migration: into the source, and into the target.
        `vectors` are for the source (its model). The target gets the same vectors in rebuild
        mode; in re-embed mode pass `new_vectors` (the new model's), or the documents are
        embedded here."""
        s = self.state
        self._need("started", "backfilled", "verified")
        self._open_source()
        self._open_target()
        out: list[str] = self.client.insert(s.source, vectors, list(ids), metadata, documents)
        if s.mode == "rebuild":
            tv = _unit(np.asarray(vectors, dtype=np.float64))
        elif new_vectors is not None:
            tv = _unit(np.asarray(new_vectors, dtype=np.float64))
        else:
            if documents is None or not all(isinstance(d, str) for d in documents):
                raise MigrationError("re-embed needs new_vectors or a text document per vector")
            assert self.embed is not None
            tv = _unit(np.asarray(self.embed(list(documents)), dtype=np.float64))  # type: ignore[arg-type]
        self.client.insert(s.target, tv, list(ids), metadata, documents)
        if s.phase == "verified":
            s.phase, s.verify = "backfilled", None
            self._save()
        return out

    def delete(self, ids: Sequence[str]) -> None:
        """The application's delete during a migration: from both collections."""
        s = self.state
        self._need("started", "backfilled", "verified")
        self._open_source()
        self._open_target()
        self.client.delete(s.source, list(ids))
        self.client.delete(s.target, list(ids))

    # ---- verify ---------------------------------------------------------------------------
    def verify(
        self,
        *,
        sample: int = 20,
        k: int = 10,
        min_recall: float = 0.9,
        min_self: float = 0.95,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Run the gates; the result is stored in the state file and returned. Raises
        `MigrationError` (after storing the result) if a gate failed."""
        s = self.state
        self._need("started", "backfilled", "verified")
        self._open_source()
        self._open_target()
        src = self.client.get_collection(s.source)
        tgt = self.client.get_collection(s.target)
        src_live = src.vector_count - src.deleted_count
        tgt_live = tgt.vector_count - tgt.deleted_count
        recs = list(self.client.iter_records(s.target))
        missing = sum(1 for r in recs if r.vector is None)
        have = [r for r in recs if r.vector is not None]
        res: dict[str, Any] = {
            "source_live": src_live,
            "target_live": tgt_live,
            "records_missing": missing,
            "k": k,
        }
        gates = {
            "counts_equal": src_live == tgt_live,
            "all_have_records": missing == 0,
        }
        if have:
            mat = _unit(np.stack([r.vector for r in have]))
            rng = np.random.default_rng(seed)
            pick = rng.choice(len(have), size=min(sample, len(have)), replace=False)
            self_ok = 0
            recall_sum = 0.0
            overlap_sum, overlap_n = 0.0, 0
            wanted = {have[i].id for i in pick}
            src_vecs = {
                r.id: r.vector
                for r in self.client.iter_records(s.source)
                if r.id in wanted and r.vector is not None
            }
            for i in pick:
                q = mat[i]
                top = {h.id for h in self.client.search(s.target, q, k=k)}
                if have[i].id in {h.id for h in self.client.search(s.target, q, k=5)}:
                    self_ok += 1
                truth = {have[j].id for j in np.argsort(-(mat @ q), kind="stable")[:k]}
                recall_sum += len(top & truth) / max(1, len(truth))
                sv = src_vecs.get(have[i].id)
                if sv is not None:  # same id, the source's own vector, the source's own search
                    old = {h.id for h in self.client.search(s.source, _unit(sv), k=k)}
                    overlap_sum += len(top & old) / max(1, k)
                    overlap_n += 1
            n = len(pick)
            res["self_retrieval"] = self_ok / n
            res["recall"] = recall_sum / n
            res["overlap_with_source"] = overlap_sum / overlap_n if overlap_n else None
            res["sample"] = n
            gates["self_retrieval"] = res["self_retrieval"] >= min_self
            gates["recall"] = res["recall"] >= min_recall
        else:
            gates["self_retrieval"] = gates["recall"] = False
        res["gates"] = gates
        res["passed"] = all(gates.values())
        res["at"] = self._now()
        s.verify = res
        if res["passed"]:
            s.phase = "verified"
        self._save()
        if not res["passed"]:
            failed = ", ".join(g for g, ok in gates.items() if not ok)
            raise MigrationError(f"verify failed: {failed}")
        return res

    # ---- switch ---------------------------------------------------------------------------
    def switch(self, *, force: bool = False) -> str:
        """Move the alias to the target (one compare-and-swap) and return a one-line outcome.
        Without an alias, or in local mode, the switch is recorded and the caller points the
        application at the new name."""
        s = self.state
        if s.phase == "switched":
            return self._switch_text()
        if not force:
            self._need("verified")
        else:
            self._need("started", "backfilled", "verified")
        if s.alias and _supports_aliases(self.client):
            try:
                self.client.switch_alias(s.alias, s.target, s.source)
            except AliasConflictError:
                raise MigrationError(
                    "the alias does not point at the source any more; look at the aliases "
                    "(someone switched it), then abort or plan again"
                ) from None
            s.switched_alias = True
        s.phase = "switched"
        s.switched_at = self._now()
        self._save()
        return self._switch_text()

    def _switch_text(self) -> str:
        s = self.state
        if s.switched_alias:
            return f"alias {s.alias!r} now points at {s.target!r}"
        return (
            f"no alias was moved: point the application at {s.target!r} "
            "(aliases need a server and --alias)"
        )

    # ---- finish / abort -------------------------------------------------------------------
    def finish(self, *, force: bool = False) -> str:
        """Drop the source after the drain period. Its key file stays on disk."""
        s = self.state
        self._need("switched")
        assert s.switched_at is not None
        due = s.switched_at + s.drain_hours * 3600
        if not force and self._now() < due:
            left = (due - self._now()) / 3600
            raise MigrationError(f"the drain period is not over: {left:.1f} hours left")
        if s.switched_alias:
            cur = {a.name: a for a in self.client.list_aliases()}.get(s.alias or "")
            if cur is None or cur.target != s.target:
                raise MigrationError("the alias no longer points at the target; not dropping")
        if s.source in self.client.list_collections():
            self.client.drop_collection(s.source)
        s.phase = "finished"
        s.finished_at = self._now()
        self._save()
        return f"dropped {s.source!r}; keep {s.source_key_file} as long as you keep backups"

    def abort(self) -> str:
        """Drop the target. Not possible after the switch (switch the alias back yourself)."""
        s = self.state
        if s.phase in ("switched", "finished"):
            raise MigrationError("the alias was switched: switch it back first, then plan again")
        if s.phase == "aborted":
            return "already aborted"
        if s.target in self.client.list_collections():
            self.client.drop_collection(s.target)
        s.phase = "aborted"
        self._save()
        return f"dropped {s.target!r}; the source is untouched"

    def status(self) -> dict[str, Any]:
        return status_of(self.state)


def status_of(s: State) -> dict[str, Any]:
    return {
        "source": s.source,
        "target": s.target,
        "mode": s.mode,
        "phase": s.phase,
        "alias": s.alias,
        "backfilled": s.backfilled,
        "verified": bool(s.verify and s.verify.get("passed")),
    }


# ---- command line ----------------------------------------------------------------------------
def _passphrase(env: str, prompt: str) -> str:
    v = os.environ.get(env)
    if v is not None:
        return v
    if not sys.stdin.isatty():
        raise MigrationError(f"set {env} (the passphrase) or run in a terminal")
    return getpass.getpass(prompt)


def _client(args: argparse.Namespace) -> Any:
    from ombra import Client

    if args.local:
        return Client.local(args.local)
    if not args.address:
        raise MigrationError("give --address (or --local DIR)")
    cert = Path(args.root_certificates).read_bytes() if args.root_certificates else None
    return Client(
        args.address,
        api_key=os.environ.get("OMBRA_API_KEY"),
        root_certificates=cert,
        insecure=args.insecure,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ombra-migrate",
        description="Move a collection to a new embedding model, preset or key set with no "
        "downtime. The API key comes from OMBRA_API_KEY; passphrases from OMBRA_PASSPHRASE "
        "(source key file) and OMBRA_TARGET_PASSPHRASE (target key file) or a prompt.",
    )
    p.add_argument(
        "step",
        choices=["plan", "start", "backfill", "verify", "switch", "finish", "abort", "status"],
    )
    p.add_argument("--state", default=DEFAULT_STATE, help="state file (default %(default)s)")
    p.add_argument("--address", help="server host:port")
    p.add_argument("--root-certificates", help="PEM file of the server's CA")
    p.add_argument("--insecure", action="store_true", help="plain text, loopback test servers only")
    p.add_argument("--local", metavar="DIR", help="a local-mode directory instead of a server")
    p.add_argument("--embedder", metavar="pkg.mod:fn", help="re-embed with this callable")
    p.add_argument("--force", action="store_true", help="skip the verify gate or the drain wait")
    plan = p.add_argument_group("plan")
    plan.add_argument("--source", help="collection to migrate")
    plan.add_argument("--source-key-file")
    plan.add_argument("--target-key-file", help="new key file to write (must not exist)")
    plan.add_argument("--alias", help="alias the application opens; switched at `switch`")
    plan.add_argument("--target-preset", help="preset of the target (default: the source's)")
    plan.add_argument("--model", help="label of the embedding model (required with --embedder)")
    plan.add_argument("--filter-fields", default="", help="comma-separated names, creation order")
    plan.add_argument("--drain-hours", type=float, default=DEFAULT_DRAIN_HOURS)
    plan.add_argument("--clusters", type=int, help="cluster count of the target")
    v = p.add_argument_group("backfill and verify")
    v.add_argument("--batch-size", type=int, default=1024)
    v.add_argument("--sample", type=int, default=20, help="verify: queries to run")
    v.add_argument("-k", type=int, default=10, help="verify: results per query")
    v.add_argument("--min-recall", type=float, default=0.9)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.step == "status":
            st = load_state(args.state)
            print(json.dumps(status_of(st)))
            return 0
        client = _client(args)
        try:
            return _run(client, args)
        finally:
            client.close()
    except OmbraError as e:
        print(f"ombra-migrate: {e}", file=sys.stderr)
        return 1


def _run(client: Any, args: argparse.Namespace) -> int:
    src_pw = _passphrase("OMBRA_PASSPHRASE", "source key file passphrase: ")
    tgt_pw = (
        _passphrase("OMBRA_TARGET_PASSPHRASE", "target key file passphrase: ")
        if args.step in ("plan", "start", "backfill", "verify")
        else ""
    )
    if args.step == "plan":
        if not (args.source and args.source_key_file and args.target_key_file):
            raise MigrationError("plan needs --source, --source-key-file and --target-key-file")
        m = Migration.plan(
            client,
            args.source,
            source_key_file=args.source_key_file,
            target_key_file=args.target_key_file,
            source_passphrase=src_pw,
            target_passphrase=tgt_pw,
            state_path=args.state,
            filter_fields=[f for f in args.filter_fields.split(",") if f],
            alias=args.alias,
            target_preset=args.target_preset,
            embedder=args.embedder,
            model=args.model,
            drain_hours=args.drain_hours,
            n_clusters=args.clusters,
            batch_size=args.batch_size,
        )
        print(f"planned {m.state.source} -> {m.state.target} ({m.state.mode})")
        return 0
    m = Migration.resume(
        client,
        state_path=args.state,
        source_passphrase=src_pw,
        target_passphrase=tgt_pw,
        embed=load_embedder(args.embedder) if args.embedder else None,
        batch_size=args.batch_size,
    )
    if args.step == "start":
        m.start()
        print(f"started {m.state.target}")
    elif args.step == "backfill":
        n = m.backfill()
        print(f"copied {n} vectors")
    elif args.step == "verify":
        res = m.verify(sample=args.sample, k=args.k, min_recall=args.min_recall)
        print(json.dumps({k: v for k, v in res.items() if k != "at"}))
    elif args.step == "switch":
        print(m.switch(force=args.force))
    elif args.step == "finish":
        print(m.finish(force=args.force))
    elif args.step == "abort":
        print(m.abort())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
