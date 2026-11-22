# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Leakage harness: record what the server sees while the client runs an operation.

    python core/bench/leakage.py [--n 200] [--d 384] [--levels fast decoy strict]
                                 [--queries 3] [--attack-ns 1 5 10] [--out leakage.json]

A `RecordingStub` wraps the stub the client talks to (the gRPC stub or the local-mode stub) from the
outside; no library code changes. `record(client_or_collection, fn)` swaps it in for the duration of
`fn` and returns a `View`: the ordered calls of the operation. For each call the view holds the
method name, the serialized size and a structural summary of every request message (a `bytes` field
becomes its length, so ciphertexts are opaque; scalars, enums, strings and repeated ints stay), the
serialized size of every response, and a coarse timing class.

What is stored beyond the summary: the raw bytes and string fields of the REQUESTS, so that
`contains_any` can look for ids inside any field. Pass `keep_payloads=False` to `record` to drop
them (the id helpers then raise). Responses are never kept, only their size and kind: they come
from the server, so they say nothing about what the server learns.

Streams stay lazy: a request iterator is wrapped by a generator that records each message as the
transport pulls it, and a response iterator by an object that records each message as the caller
reads it (`cancel` and other attributes are passed through, so the Search stream's replay and retry
keep working). A `Replayable` stays a `Replayable`.

Use synthetic data only. Nothing here prints a vector, a query or a score; the CLI writes
aggregates.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

try:  # a request stream that can be sent again; duck-typed fallback keeps the module standalone
    from ombra.retry import Replayable
except ImportError:  # pragma: no cover

    class Replayable:  # type: ignore[no-redef]
        def __init__(self, factory: Callable[[], Iterator[Any]]) -> None:
            self._factory = factory

        def __iter__(self) -> Iterator[Any]:
            return iter(self._factory())


# Fields that differ from run to run and say nothing about the query: the key tag is derived from
# the freshly generated key. Request ids and deadlines never reach the summary (they are gRPC
# metadata and call options, kept apart in `Call.options`); nonces and seeds travel inside
# ciphertexts, which are opaque here.
VOLATILE_FIELDS = frozenset({"key_tag"})
_ID_NAMES = frozenset({"id", "ids"})
MIN_NEEDLE = 8  # shorter ids match random ciphertext bytes by chance


# ---- one recorded message and call ---------------------------------------------------------------
def _is_repeated(fd: Any) -> bool:
    rep = getattr(fd, "is_repeated", None)
    return bool(rep) if rep is not None else fd.label == fd.LABEL_REPEATED


def summarize(msg: Any, path: str = "", payload: dict[str, list[bytes]] | None = None) -> dict:
    """Structural summary of a protobuf message: bytes become ("bytes", length); strings, enums,
    numbers and repeated scalars are kept; sub-messages are summarized recursively. If `payload`
    is given, the raw bytes and string values are appended to it under their dotted field path."""
    out: dict[str, Any] = {}
    for fd, val in msg.ListFields():
        p = f"{path}.{fd.name}" if path else fd.name
        rep = _is_repeated(fd)
        if fd.type == fd.TYPE_MESSAGE:
            out[fd.name] = (
                tuple(summarize(v, p, payload) for v in val) if rep else summarize(val, p, payload)
            )
        elif fd.type == fd.TYPE_BYTES:
            vals = list(val) if rep else [val]
            if payload is not None:
                payload.setdefault(p, []).extend(bytes(v) for v in vals)
            out[fd.name] = tuple(("bytes", len(v)) for v in vals) if rep else ("bytes", len(val))
        elif fd.type == fd.TYPE_STRING:
            vals = list(val) if rep else [val]
            if payload is not None:
                payload.setdefault(p, []).extend(v.encode() for v in vals)
            out[fd.name] = tuple(vals) if rep else val
        else:
            out[fd.name] = tuple(val) if rep else val
    return out


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((k, _freeze(v)) for k, v in sorted(value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(v) for v in value)
    return value


@dataclass
class Msg:
    """One request or response message: its type, serialized size and (requests) summary."""

    kind: str
    size: int
    fields: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, list[bytes]] | None = None


@dataclass
class Call:
    """One RPC: the messages sent and received, how long it took and the call options."""

    method: str
    requests: list[Msg] = field(default_factory=list)
    responses: list[Msg] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)  # timeout, metadata: never in shape()
    error: str | None = None  # class name of the exception that ended the call
    attempts: int = 1  # times a Replayable request stream was started
    start: float = 0.0
    end: float = 0.0

    @property
    def millis(self) -> float:
        return max(0.0, (self.end - self.start) * 1000.0)

    @property
    def timing_class(self) -> int:
        """floor(log2(1 + milliseconds)): 0 under 1 ms, 1 for 1-3 ms, 2 for 3-7 ms, ..."""
        return int(math.log2(1.0 + self.millis))

    @property
    def bytes_up(self) -> int:
        return sum(m.size for m in self.requests)

    @property
    def bytes_down(self) -> int:
        return sum(m.size for m in self.responses)


class _Sink:
    """Where a RecordingStub puts its calls; `active` goes False when recording is detached."""

    def __init__(self, keep_payloads: bool) -> None:
        self.calls: list[Call] = []
        self.keep_payloads = keep_payloads
        self.active = True
        self.lock = threading.Lock()


def _is_message(obj: Any) -> bool:
    return hasattr(obj, "SerializeToString") and hasattr(obj, "ListFields")


def _msg_of(sink: _Sink, m: Any, with_fields: bool) -> Msg:
    kind = type(m).__name__
    size = m.ByteSize()
    if not with_fields:
        return Msg(kind, size)
    payload: dict[str, list[bytes]] | None = {} if sink.keep_payloads else None
    return Msg(kind, size, summarize(m, "", payload), payload)


class _RecordingIter:
    """A response stream seen from the caller; records each message as it is read."""

    def __init__(self, inner: Any, sink: _Sink, call: Call) -> None:
        self._inner, self._sink, self._call = inner, sink, call
        self._it = iter(inner)

    def __iter__(self) -> _RecordingIter:
        return self

    def __next__(self) -> Any:
        try:
            m = next(self._it)
        except StopIteration:
            self._call.end = time.perf_counter()
            raise
        except BaseException as exc:
            self._call.error = type(exc).__name__
            self._call.end = time.perf_counter()
            raise
        if self._sink.active:
            with self._sink.lock:
                self._call.responses.append(_msg_of(self._sink, m, False))
        self._call.end = time.perf_counter()
        return m

    def __getattr__(self, name: str) -> Any:  # cancel, code, ... of the real call object
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._inner, name)


class RecordingStub:
    """Wraps a stub and records every RPC into a sink. Upper-case attributes are RPC methods;
    everything else (retry_runner, retry_policy) is passed through to the wrapped stub."""

    def __init__(self, inner: Any, sink: _Sink) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_sink", sink)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        inner = object.__getattribute__(self, "_inner")
        target = getattr(inner, name)
        if not name[:1].isupper() or not callable(target):
            return target
        sink: _Sink = object.__getattribute__(self, "_sink")
        return lambda *args, **kwargs: _invoke(sink, name, target, args, kwargs)

    def __repr__(self) -> str:
        return "RecordingStub()"


def _record_requests(sink: _Sink, call: Call, items: Iterable[Any]) -> Iterator[Any]:
    for m in items:
        if sink.active:
            with sink.lock:
                call.requests.append(_msg_of(sink, m, True))
        yield m


def _invoke(sink: _Sink, method: str, target: Callable, args: tuple, kwargs: dict) -> Any:
    call = Call(method, start=time.perf_counter())
    call.end = call.start
    call.options = {k: v for k, v in kwargs.items()}
    if sink.active:
        with sink.lock:
            sink.calls.append(call)

    def wrap(arg: Any) -> Any:
        if _is_message(arg):
            if sink.active:
                with sink.lock:
                    call.requests.append(_msg_of(sink, arg, True))
            return arg
        if isinstance(arg, Replayable):

            def factory() -> Iterator[Any]:
                with sink.lock:
                    call.attempts += 1
                return _record_requests(sink, call, arg)

            call.attempts = 0
            return Replayable(factory)
        if hasattr(arg, "__next__") or isinstance(arg, list | tuple):
            return _record_requests(sink, call, arg)
        return arg

    try:
        result = target(*[wrap(a) for a in args], **kwargs)
    except BaseException as exc:
        call.error = type(exc).__name__
        call.end = time.perf_counter()
        raise
    if _is_message(result):
        if sink.active:
            with sink.lock:
                call.responses.append(_msg_of(sink, result, False))
    elif isinstance(result, list | tuple):
        if sink.active:
            with sink.lock:
                call.responses.extend(_msg_of(sink, m, False) for m in result)
    elif hasattr(result, "__next__") or hasattr(result, "__iter__"):
        result = _RecordingIter(result, sink, call)
    call.end = time.perf_counter()
    return result


# ---- the view ------------------------------------------------------------------------------------
@dataclass
class View:
    """What the server sees of one operation: the ordered calls."""

    calls: list[Call] = field(default_factory=list)

    # sizes
    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def bytes_up(self) -> int:
        return sum(c.bytes_up for c in self.calls)

    @property
    def bytes_down(self) -> int:
        return sum(c.bytes_down for c in self.calls)

    # identifiers the server sees
    def _gather(self, method: str, path: str) -> list[bytes]:
        out: list[bytes] = []
        for c in self.calls:
            if c.method != method:
                continue
            for m in c.requests:
                if m.payload is None:
                    raise ValueError("view was recorded with keep_payloads=False")
                out.extend(m.payload.get(path, []))
        return out

    def blob_ids(self) -> dict[str, list[bytes]]:
        """Blob ids per call kind: GetBlob, PutBlob (header) and DeleteBlobs."""
        return {
            "GetBlob": self._gather("GetBlob", "id"),
            "PutBlob": self._gather("PutBlob", "header.id"),
            "DeleteBlobs": self._gather("DeleteBlobs", "ids"),
        }

    def record_ids(self) -> dict[str, list[bytes]]:
        """Record ids sent to Insert (block headers) and Delete."""
        return {
            "Insert": self._gather("Insert", "header.ids"),
            "Delete": self._gather("Delete", "ids"),
        }

    def collections(self) -> set[str]:
        """Collection names named in any request."""
        out: set[str] = set()

        def walk(d: dict) -> None:
            for k, v in d.items():
                if k == "collection" and isinstance(v, str):
                    out.add(v)
                elif isinstance(v, dict):
                    walk(v)
                elif isinstance(v, tuple):
                    for x in v:
                        if isinstance(x, dict):
                            walk(x)

        for c in self.calls:
            for m in c.requests:
                walk(m.fields)
        return out

    def requested_clusters(self) -> list[set[int]]:
        """Cluster ids per Search round: one set for each SearchChosen message, in order. A
        round of more than 4,096 clusters is sent as several messages and counts as several."""
        out: list[set[int]] = []
        for c in self.calls:
            if c.method != "Search":
                continue
            for m in c.requests:
                chosen = m.fields.get("chosen")
                if m.kind == "SearchRequest" and chosen is not None:
                    out.append({int(i) for i in chosen.get("cluster_ids", ())})
        return out

    def shape(
        self,
        *,
        include_clusters: bool = False,
        include_ids: bool = False,
        include_timing: bool = False,
        drop: Iterable[str] = VOLATILE_FIELDS,
    ) -> tuple:
        """A canonical hashable form for equality checks.

        Per call: method, then for each request its type, size and summary, then the size of each
        response. Fields named in `drop` (default: the key tag), request ids, deadlines and every
        ciphertext byte are left out. Cluster ids are replaced by their count unless
        `include_clusters`. `include_ids` adds the raw values of the fields named `id` and `ids`
        of every request (needs payloads): without it two fetches of different records have the
        same shape, because a bytes field is only a length. `include_timing` adds the timing
        class of each call.
        """
        skip = set(drop)

        def clean(v: Any) -> Any:
            if isinstance(v, dict):
                out = {}
                for k, x in v.items():
                    if k in skip:
                        continue
                    if k == "cluster_ids" and not include_clusters:
                        out[k] = len(x)
                    else:
                        out[k] = clean(x)
                return out
            if isinstance(v, tuple):
                return tuple(clean(x) for x in v)
            return v

        def id_values(m: Msg) -> tuple:
            if m.payload is None:
                raise ValueError("view was recorded with keep_payloads=False")
            return tuple(
                sorted((p, tuple(v)) for p, v in m.payload.items() if p.split(".")[-1] in _ID_NAMES)
            )

        rows = []
        for c in self.calls:
            reqs = tuple(
                (m.kind, m.size, _freeze(clean(m.fields)))
                + ((id_values(m),) if include_ids else ())
                for m in c.requests
            )
            row: list[Any] = [
                c.method,
                reqs,
                tuple((m.kind, m.size) for m in c.responses),
            ]
            if include_timing:
                row.append(c.timing_class)
            rows.append(tuple(row))
        return tuple(rows)


# ---- recording -----------------------------------------------------------------------------------
def _slots(target: Any) -> list[tuple[Any, str, Any]]:
    """(object, attribute, current stub) for every place the client keeps a stub."""
    found: list[tuple[Any, str, Any]] = []
    seen: set[tuple[int, str]] = set()

    def add(obj: Any, attr: str) -> None:
        stub = getattr(obj, attr, None)
        if stub is not None and (id(obj), attr) not in seen:
            seen.add((id(obj), attr))
            found.append((obj, attr, stub))

    def add_collection(col: Any) -> None:
        add(col, "stub")
        blobs = getattr(col, "blobs", None)
        if blobs is not None:
            add(blobs, "_stub")

    core = getattr(target, "_core", None) or getattr(target, "_sync", None)
    if core is not None:
        add(core._t, "stub")
        for col, _names in list(getattr(core, "_open", {}).values()):
            add_collection(col)
    elif hasattr(target, "stub"):
        add_collection(target)
    else:
        raise TypeError("expected an ombra Client or Collection")
    return found


@contextlib.contextmanager
def attach(target: Any, *, keep_payloads: bool = True) -> Iterator[View]:
    """Record every RPC of `target` (a Client or a Collection) while the block runs. The view is
    filled as calls happen and is complete when the block ends."""
    sink = _Sink(keep_payloads)
    view = View(sink.calls)
    slots = _slots(target)
    for obj, attr, stub in slots:
        setattr(obj, attr, RecordingStub(stub, sink))
    try:
        yield view
    finally:
        sink.active = False
        for obj, attr, stub in slots:
            setattr(obj, attr, stub)
        for obj, attr, stub in _slots(target):  # collections opened inside the block
            if isinstance(stub, RecordingStub):
                setattr(obj, attr, object.__getattribute__(stub, "_inner"))


def record(target: Any, fn: Callable[[], Any], *, keep_payloads: bool = True) -> View:
    """Run `fn()` and return the server's view of it. `target` is the Client (or Collection)
    that `fn` talks through."""
    with attach(target, keep_payloads=keep_payloads) as view:
        fn()
    return view


def contains_any(view: View, ids: Iterable[bytes | str], *, allow_short: bool = False) -> bool:
    """Whether any of `ids` appears, as raw bytes, inside any bytes or string field of any request
    of the view (not only in fields named id). Ids shorter than 8 bytes are refused: they match
    random ciphertext bytes by chance; use long synthetic ids."""
    needles = [i.encode() if isinstance(i, str) else bytes(i) for i in ids]
    if not allow_short and any(len(n) < MIN_NEEDLE for n in needles):
        raise ValueError(f"ids shorter than {MIN_NEEDLE} bytes give false matches")
    needles = [n for n in needles if n]
    for c in view.calls:
        for m in c.requests:
            if m.payload is None:
                raise ValueError("view was recorded with keep_payloads=False")
            for values in m.payload.values():
                for v in values:
                    if any(n in v for n in needles):
                        return True
    return False


def requested_clusters(view: View) -> list[set[int]]:
    return view.requested_clusters()


# ---- the intersection attack ---------------------------------------------------------------------
def _score(inter: set[int], real: set[int]) -> dict[str, Any]:
    hit = len(inter & real)
    return {
        "size": len(inter),
        "success": bool(inter) and inter <= real,
        "precision": hit / len(inter) if inter else 0.0,
        "recall": hit / len(real) if real else 0.0,
    }


def _rounds(v: View | Sequence[set[int]]) -> list[set[int]]:
    return v.requested_clusters() if isinstance(v, View) else [set(r) for r in v]


def intersection_attack(
    views: Sequence[View | Sequence[set[int]]], real: Iterable[int]
) -> dict[str, dict[str, Any]]:
    """The attacker sees several searches whose real cluster sets are related. It intersects the
    requested sets of the searches: of the first round of each, and of the union of the rounds of
    each. For each it reports `size` of the intersection I, `success` (I is non-empty and inside
    `real`), `precision` |I & real| / |I| and `recall` |I & real| / |real|."""
    real_set = {int(c) for c in real}
    per = [_rounds(v) for v in views]
    if not per or any(not r for r in per):
        raise ValueError("every search needs at least one requested round")
    first = set.intersection(*[set(r[0]) for r in per])
    union = set.intersection(*[set().union(*r) for r in per])
    return {"first_round": _score(first, real_set), "union": _score(union, real_set)}


def attack_curve(
    view_lists: Sequence[Sequence[View | Sequence[set[int]]]],
    reals: Sequence[Iterable[int]] | Iterable[int],
    ns: Sequence[int] = (1, 10, 100, 1000),
) -> dict[int, dict[str, dict[str, float]]]:
    """Average `intersection_attack` over trials. `view_lists[t]` holds the searches of trial t
    (at least max(ns) of them), `reals[t]` its real clusters (or one set for all trials). For each
    n the attack uses the first n searches of every trial. Returns, per n and per kind, the mean
    of size, success (a rate), precision and recall."""
    trials = len(view_lists)
    if trials == 0:
        raise ValueError("no trials")
    real_list = list(reals)
    if real_list and all(isinstance(r, int) for r in real_list):
        real_list = [real_list] * trials  # one set for every trial
    if len(real_list) != trials:
        raise ValueError("one real cluster set per trial is needed")
    out: dict[int, dict[str, dict[str, float]]] = {}
    for n in ns:
        sums: dict[str, dict[str, float]] = {}
        for views, real in zip(view_lists, real_list, strict=True):
            if len(views) < n:
                raise ValueError(f"a trial has {len(views)} searches, fewer than n={n}")
            res = intersection_attack(views[:n], real)
            for kind, r in res.items():
                acc = sums.setdefault(kind, dict.fromkeys(r, 0.0))
                for key, val in r.items():
                    acc[key] += float(val)
        out[n] = {kind: {k: v / trials for k, v in acc.items()} for kind, acc in sums.items()}
    return out


# ---- command line --------------------------------------------------------------------------------
def _unit(x: Any) -> Any:
    import numpy as np

    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def _synthetic(n: int, d: int, groups: int, seed: int) -> Any:
    import numpy as np

    rng = np.random.default_rng(seed)
    centers = _unit(rng.standard_normal((groups, d)))
    x = centers[rng.integers(0, groups, n)] + 0.35 * rng.standard_normal((n, d)) / math.sqrt(d)
    return _unit(x)


def _noisy(q: Any, scale: float, rng: Any) -> Any:

    return _unit(q + scale * rng.standard_normal(q.shape) / math.sqrt(len(q)))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def run(args: argparse.Namespace) -> dict[str, Any]:
    import tempfile
    from pathlib import Path

    import numpy as np
    import ombra

    rng = np.random.default_rng(args.seed + 1)
    x = _synthetic(args.n, args.d, args.groups, args.seed)
    ids = [f"synthetic-record-{i:08d}" for i in range(args.n)]
    report: dict[str, Any] = {
        "params": {
            "n": args.n,
            "d": args.d,
            "clusters": args.clusters,
            "queries": args.queries,
            "attack_ns": args.attack_ns,
            "attack_trials": args.attack_trials,
            "noise": args.noise,
        },
        "levels": {},
    }
    with tempfile.TemporaryDirectory() as t:
        client = ombra.Client.local(Path(t) / "data")
        try:
            client.create_collection(
                "leak",
                x,
                ids,
                key_file=Path(t) / "leak.key",
                passphrase="leakage-harness",  # noqa: S106
                scrypt_params=(2**14, 8, 1),
                n_clusters=args.clusters,
            )
            col, _ = client._core._open["leak"]
            report["params"]["cluster_count"] = col.cluster_count
            qs = [x[int(rng.integers(0, args.n))] for _ in range(args.queries)]
            for level in args.levels:
                for rerank in (False, True):
                    views = []
                    for q in qs:
                        views.append(
                            record(
                                client,
                                lambda q=q, level=level, rerank=rerank: client.search(
                                    "leak", q, k=5, level=level, rerank=rerank
                                ),
                            )
                        )
                    shapes = {v.shape(include_clusters=True, include_ids=True) for v in views}
                    report["levels"][f"{level}/rerank={rerank}"] = {
                        "calls_mean": _mean([v.n_calls for v in views]),
                        "bytes_up_mean": _mean([v.bytes_up for v in views]),
                        "bytes_down_mean": _mean([v.bytes_down for v in views]),
                        "record_ids_in_view": any(contains_any(v, ids) for v in views),
                        "blob_ids_fetched_mean": _mean(
                            [len(v.blob_ids()["GetBlob"]) for v in views]
                        ),
                        "rounds_mean": _mean([len(v.requested_clusters()) for v in views]),
                        "identical_across_queries": len(shapes) == 1,
                        "distinct_shapes": len(shapes),
                    }
            if "decoy" in args.levels:
                report["intersection_attack_decoy"] = _decoy_attack(client, col, x, args, rng)
        finally:
            client.close()
    return report


def _decoy_attack(client: Any, col: Any, x: Any, args: argparse.Namespace, rng: Any) -> dict:
    from ombra.search import run_search

    max_n = max(args.attack_ns)
    view_lists, reals = [], []
    for _ in range(args.attack_trials):
        base = x[int(rng.integers(0, len(x)))]
        real = set(run_search(col, base, 5, level="decoy", rerank=False).probed)
        views = []
        for _ in range(max_n):
            q = _noisy(base, args.noise, rng)
            views.append(
                record(
                    client,
                    lambda q=q: run_search(col, q, 5, level="decoy", rerank=False),
                    keep_payloads=False,
                )
            )
        view_lists.append(views)
        reals.append(real)
    curve = attack_curve(view_lists, reals, args.attack_ns)
    return {str(n): v for n, v in curve.items()}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Record what the server sees during searches on a synthetic local-mode "
        "collection and print aggregates (never vectors, queries, scores or real data)."
    )
    p.add_argument("--n", type=int, default=200, help="vectors in the collection (default 200)")
    p.add_argument("--d", type=int, default=384, help="dimension (default 384)")
    p.add_argument("--groups", type=int, default=12, help="gaussian groups in the data")
    p.add_argument("--clusters", type=int, default=16, help="clusters of the collection")
    p.add_argument("--levels", nargs="+", default=["fast", "decoy", "strict"])
    p.add_argument("--queries", type=int, default=3, help="searches per level and mode")
    p.add_argument(
        "--attack-ns",
        type=int,
        nargs="+",
        default=[1, 5, 10],
        help="related-query counts of the Decoy intersection attack (for example 1 10 100 1000)",
    )
    p.add_argument("--attack-trials", type=int, default=2, help="repeats averaged per count")
    p.add_argument("--noise", type=float, default=0.05, help="relative noise of related queries")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="leakage.json", help="where to write the JSON aggregates")
    args = p.parse_args(argv)
    for lv in args.levels:
        if lv not in ("fast", "decoy", "strict"):
            p.error(f"unknown level {lv!r}")
    report = run(args)
    text = json.dumps(report, indent=2, sort_keys=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
