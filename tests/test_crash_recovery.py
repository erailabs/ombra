# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Process-level crash tests: kill -9 the real (CPU) `ombra_server` while it writes, restart it on
the same data directory, and check what survived.

Every case does the same: copy a ready data directory, run one operation (insert, delete,
compaction, block replacement, re-clustering, key upload, blobs, drop and re-create) against the
server, kill the server in the middle, restart it, run `ombra_verify`, and check:

* every write the client got an answer for is there, exactly once;
* every write without an answer is all there or all absent (a block is never partial, an id is
  never in two blocks, a delete request applies to all its ids or to none);
* the keys parse and pass the server's checks (the collection can take an insert again) and are
  byte-identical to the uploaded ones;
* a blob holds an old or a new value, never a mix;
* after the restart there is no orphan file, and the client can go on writing.

Two kinds of kill point. "random" cases kill at a random wall-clock time during the operation
(seed in the test id; rerun one with `-k "insert-s123456"`, set the base seed of a whole run with
OMBRA_CRASH_SEED, the number of random cases with OMBRA_CRASH_ITERS, default 20). "point" cases
arm one deterministic CrashPoint of `ombra/storage/failpoint.hpp` with OMBRA_CRASH_POINT, which
the server reads only in a build with OMBRA_ENABLE_TEST_HOOKS (the default; the release image turns
it off); every point must stop the server with exit code 42.

Client-side encryption at d = 384 takes seconds per block on a CPU, so it runs once per session:
a block of columns is encrypted into a cache directory and `KeyKit.encrypt_columns` returns it for
every block (the server never looks at plaintext). Set OMBRA_CRASH_CACHE to keep that cache
between runs and OMBRA_CRASH_DIR to put the (large) work directories somewhere other than /tmp.
Synthetic data only.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")
pytest.importorskip("openfhe")
pytest.importorskip("faiss")

from server_harness import ServerProcess, find_server_binary  # noqa: E402

pytestmark = [pytest.mark.openfhe, pytest.mark.crash]

_SERVER = find_server_binary()
if _SERVER is None:
    pytest.skip(
        "ombra_server (CPU build) not found: run `make build-cpu` or set OMBRA_SERVER_BIN",
        allow_module_level=True,
    )
_VERIFY = Path(
    os.environ.get("OMBRA_VERIFY_BIN", _SERVER.parent.parent / "storage" / "ombra_verify")
)
if not _VERIFY.is_file():
    pytest.skip(f"ombra_verify not found at {_VERIFY}", allow_module_level=True)

import grpc  # noqa: E402
from ombra import RetryPolicy  # noqa: E402
from ombra._proto import load_stubs  # noqa: E402
from ombra.blobstore import BUCKET_SIZES, KIND_RECORD  # noqa: E402
from ombra.collection import Collection, KeyKit  # noqa: E402
from ombra.presets import derive  # noqa: E402

ITERS = int(os.environ.get("OMBRA_CRASH_ITERS", "20"))
BASE_SEED = int(os.environ.get("OMBRA_CRASH_SEED", str(int(time.time()) % 1_000_000)))
CRASH_EXIT = 42  # kCrashExitCode of failpoint.hpp
D = 384
NAME = "crash"
BLOB_SIZE = BUCKET_SIZES[0]

# The CrashPoint enum of failpoint.hpp, by operation. A test below checks that this list and the
# header agree, so a new point cannot be forgotten.
POINTS = {
    "insert": [
        "InsertBeforeIntent",
        "InsertAfterIntent",
        "InsertBeforeSegmentRename",
        "InsertAfterSegmentRename",
        "InsertAfterUnlockedWrite",
        "InsertAfterCatalog",
        "InsertAfterCommit",
    ],
    "delete": ["DeleteBeforeRecord", "DeleteAfterRecord", "DeleteAfterCatalog"],
    "drop": ["DropBeforeCatalog", "DropAfterCatalog", "DropAfterWalReset", "DropAfterFirstFile"],
    "replace": [
        "ReplaceBeforeNewFile",
        "ReplaceAfterNewFile",
        "ReplaceAfterCatalog",
        "ReplaceAfterFirstUnlink",
        "ReplaceAfterUnlink",
    ],
    "compact": ["CompactBeforeCatalog", "CompactAfterCatalog", "CompactAfterUnlink"],
    "blob_put": ["BlobBeforeRename", "BlobAfterRename", "BlobAfterCatalog"],
    "blob_delete": ["BlobDeleteAfterCatalog"],
}


# ---- session data: keys and one encrypted block, made once ----
@dataclass
class Cache:
    kit: KeyKit
    columns: list[bytes]
    base_dir: Path  # data directory with a ready collection, never changed
    base_live: set[bytes]
    base_centers: np.ndarray
    base_blobs: dict[bytes, bytes]
    key_hashes: list[str]
    work: Path


def _vectors(n: int, seed: int, hot: int) -> np.ndarray:
    """n unit vectors near axis `hot`, so that nearest-center assignment is predictable."""
    rng = np.random.default_rng(seed)
    x = rng.normal(scale=0.05, size=(n, D))
    x[:, hot] += 1.0
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _read_columns(path: Path) -> list[bytes]:
    data, out, pos = path.read_bytes(), [], 0
    while pos < len(data):
        n = int.from_bytes(data[pos : pos + 8], "little")
        out.append(data[pos + 8 : pos + 8 + n])
        pos += 8 + n
    return out


def _write_columns(path: Path, columns: list[bytes]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(b"".join(len(c).to_bytes(8, "little") + c for c in columns))
    tmp.rename(path)


def _patch(kit: KeyKit, columns: list[bytes]) -> None:
    """Every `encrypt_columns` call returns the cached envelopes (same count as asked for)."""

    def cached(cols):
        assert len(cols) <= len(columns)
        return list(columns[: len(cols)])

    kit.encrypt_columns = cached  # type: ignore[method-assign]


def _work_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="ombra-crash-", dir=os.environ.get("OMBRA_CRASH_DIR")))


def _client_kwargs() -> dict:
    return {"retry": RetryPolicy.never(), "timeout": 60.0, "stream_timeout": 300.0}


def _dir_key_hashes(data: Path) -> list[str]:
    """Sorted hashes of the key files in the data directory (what a re-upload must keep)."""
    files = [p for p in (data / "keys").iterdir() if p.is_file()]
    assert len(files) == 3, f"expected the three key files, found {len(files)}"
    return sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in files)


@pytest.fixture(scope="session")
def cache():
    cache_dir = os.environ.get("OMBRA_CRASH_CACHE")
    root = Path(cache_dir) if cache_dir else Path(tempfile.mkdtemp(prefix="ombra-crash-cache-"))
    work = _work_root()
    root.mkdir(parents=True, exist_ok=True)
    import openfhe

    derived = derive("fast", D)
    key_file, col_file = root / "kit.key", root / "columns.bin"
    if key_file.exists() and col_file.exists():
        kit = KeyKit.load(key_file, "pw", derived, openfhe.__version__, D)
        columns = _read_columns(col_file)
    else:
        kit = KeyKit(derived, openfhe.__version__, D)
        kit.save(key_file, "pw", scrypt_params=(2**4, 8, 1))
        block = _vectors(8, 1, 0)
        columns = kit.encrypt_columns([block[:, j] for j in range(D)])
        _write_columns(col_file, columns)
    _patch(kit, columns)

    # The base data directory: the collection with centers, two data blocks and, from a second
    # insert, two more (so that compaction and repack have something to work on), plus blobs.
    base = work / "base"
    server = ServerProcess(base)
    x = np.concatenate([_vectors(6, 2, 0), _vectors(6, 3, 1)])
    ids = [b"b%02d" % i for i in range(12)]
    blobs = {b"blob0": os.urandom(BLOB_SIZE)}
    with server:
        client = server.client(**_client_kwargs())
        t = client._core._t
        col = Collection.create(
            t.pb,
            t.stub,
            NAME,
            x,
            ids,
            preset="fast",
            n_clusters=2,
            kit=kit,
            openfhe_version=openfhe.__version__,
        )
        assert col.cluster_count == 2
        more = np.concatenate([_vectors(3, 4, 0), _vectors(3, 5, 1)])
        col.insert_vectors(more, [b"c%02d" % i for i in range(6)])
        for k, v in blobs.items():
            col.blobs.put(NAME, KIND_RECORD, k, v)
        centers = col.centers.copy()
    live = set(ids) | {b"c%02d" % i for i in range(6)}
    yield Cache(
        kit, columns, base / "data", live, centers, blobs, _dir_key_hashes(base / "data"), work
    )
    shutil.rmtree(work, ignore_errors=True)
    if not cache_dir:
        shutil.rmtree(root, ignore_errors=True)


# ---- one case: a copy of the base directory and a server on it ----
def _copy_dir(src: Path, dst: Path) -> None:
    """Copy `src` to `dst`. Segment and blob files are never rewritten in place by the server
    (new file, then rename), so the big ones are hard links; everything else is copied."""
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if p.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        elif p.stat().st_size > (1 << 20):
            os.link(p, dst / rel)
        else:
            shutil.copy2(p, dst / rel)


class World:
    """One case: a server on a copy of the base directory, the client, and what was acknowledged."""

    def __init__(self, cache: Cache, name: str) -> None:
        self.cache = cache
        self.root = cache.work / name
        (self.root / "data").mkdir(parents=True)
        _copy_dir(cache.base_dir, self.root / "data")
        self.server = ServerProcess(self.root)
        self.col: Collection | None = None
        self.live = set(cache.base_live)  # ids that must be alive at the end of the setup
        self.blob_values: dict[bytes, list[bytes]] = {}

    @property
    def data(self) -> Path:
        return self.root / "data"

    def start(self, env: dict[str, str] | None = None, args: list[str] | None = None) -> None:
        self.server.extra_args = list(args or [])
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            self.server.start()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        client = self.server.client(**_client_kwargs())
        t = client._core._t
        self.col = self.make_collection(t.pb, t.stub)

    def make_collection(self, pb, stub, name: str = NAME, generation: int = 0) -> Collection:
        c = self.cache
        return Collection(
            pb,
            stub,
            base_name=name,
            generation=generation,
            derived=c.kit.derived,
            cards=(),
            kit=c.kit,
            centers=c.base_centers,
        )

    def restart_plain(self) -> None:
        self.server.kill()
        self.start()

    def close(self) -> None:
        self.server.kill()
        shutil.rmtree(self.root, ignore_errors=True)


# ---- looking at the server after a restart ----
@dataclass
class BlockView:
    segment: str
    cluster: int
    ids: list[bytes]
    deleted: list[bool]


@dataclass
class Snapshot:
    infos: dict[str, object] = field(default_factory=dict)
    blocks: dict[str, list[BlockView]] = field(default_factory=dict)

    def live_ids(self, name: str = NAME) -> list[bytes]:
        return [
            i
            for b in self.blocks[name]
            if b.cluster < 2
            for i, d in zip(b.ids, b.deleted, strict=True)
            if not d
        ]

    def deleted_ids(self, name: str = NAME) -> list[bytes]:
        return [
            i
            for b in self.blocks[name]
            if b.cluster < 2
            for i, d in zip(b.ids, b.deleted, strict=True)
            if d
        ]

    def all_ids(self, name: str = NAME) -> list[bytes]:
        return [i for b in self.blocks[name] if b.cluster < 2 for i in b.ids]


def snapshot(server: ServerProcess) -> Snapshot:
    """Collections, counts, blocks and the ids of every block. Reads only block headers."""
    pb, pbg = load_stubs()
    with grpc.insecure_channel(
        server.address,
        options=[("grpc.max_receive_message_length", 1 << 30)],
    ) as ch:
        stub = pbg.OmbraStub(ch)
        snap = Snapshot()
        for info in stub.ListCollections(pb.ListCollectionsRequest(), timeout=60).collections:
            name = info.spec.name
            snap.infos[name] = info
            lb = stub.ListBlocks(pb.ListBlocksRequest(api_version=1, collection=name), timeout=60)
            assert not lb.HasField("error"), lb.error
            views = []
            for b in lb.blocks:
                stream = stub.GetBlock(
                    pb.GetBlockRequest(api_version=1, collection=name, segment=b.segment),
                    timeout=120,
                )
                first = next(iter(stream))
                stream.cancel()
                assert first.WhichOneof("part") == "header", first
                h = first.header
                assert len(h.ids) == b.n_vectors, "ListBlocks and the block header disagree"
                deleted = list(h.deleted) or [False] * len(h.ids)
                assert sum(deleted) == b.n_deleted
                views.append(BlockView(b.segment, b.cluster_id, [bytes(i) for i in h.ids], deleted))
            snap.blocks[name] = views
    return snap


def run_verify(data: Path, *, allow_warnings: bool = True) -> dict:
    import openfhe

    r = subprocess.run(  # noqa: S603 - our own binary
        [str(_VERIFY), "--data-dir", str(data), "--json", "--openfhe-version", openfhe.__version__],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode in (0, 1), f"ombra_verify failed to run: {r.stderr}"
    report = json.loads(r.stdout)
    assert r.returncode == 0, f"ombra_verify found corruption: {r.stdout}"
    findings = report.get("findings", [])
    assert not [f for f in findings if f.get("severity") == "corrupt"], findings
    if not allow_warnings:
        assert not findings, f"warnings after restart: {findings}"
    return report


# ---- what a case expects ----
@dataclass
class Insert:
    blocks: list[list[bytes]]  # ids per block
    acked: bool = False


@dataclass
class Delete:
    ids: list[bytes]
    acked: bool = False


@dataclass
class Plan:
    """The writes of a case. `inserts` and `deletes` are in the order they were sent."""

    base_live: set[bytes]
    inserts: list[Insert] = field(default_factory=list)
    deletes: list[Delete] = field(default_factory=list)
    # the live set must not change at all (repack, compaction, re-clustering)
    live_stable: bool = False


def check_snapshot(snap: Snapshot, plan: Plan, name: str = NAME) -> None:
    ids = snap.all_ids(name)
    assert len(ids) == len(set(ids)), "an id is stored twice"
    present = set(ids)
    live = set(snap.live_ids(name))
    gone = set(snap.deleted_ids(name))
    # inserts: acknowledged ones are there; the others are all there or all absent, block by block
    inserted: set[bytes] = set()
    for ins in plan.inserts:
        for blk in ins.blocks:
            here = [i in present for i in blk]
            if ins.acked:
                assert all(here), "an acknowledged insert is missing"
            else:
                assert all(here) or not any(here), "a block of an unacknowledged insert is partial"
            if all(here):
                inserted.update(blk)
    # deletes: acknowledged ones took effect; the others applied to all their ids or to none
    deleted: set[bytes] = set()
    for dl in plan.deletes:
        dead = [(i in gone) or (i not in present) for i in dl.ids]
        if dl.acked:
            assert all(dead), "an acknowledged delete is not there"
        else:
            assert all(dead) or not any(dead), "an unacknowledged delete applied to part of its ids"
        if all(dead):
            deleted.update(dl.ids)
    if plan.live_stable:
        expect = (plan.base_live | inserted) - deleted
        assert live == expect, (
            f"live set changed: missing {len(expect - live)}, extra {len(live - expect)}"
        )
    # an id that was never deleted must not have disappeared
    never_deleted = (plan.base_live | {i for i in inserted}) - {
        i for dl in plan.deletes for i in dl.ids
    }
    assert never_deleted <= live, "a live id was lost"
    # the counts agree with the blocks
    info = snap.infos[name]
    assert info.vector_count - info.deleted_count == len(live), (info, len(live))


def check_after_restart(
    world: World, plan: Plan, *, collections: set[str] | None = None
) -> Snapshot:
    """The checks every case ends with, after the server restarted on the crashed directory."""
    run_verify(world.data)  # before the restart: warnings (orphans, a torn log tail) are allowed
    world.restart_plain()
    snap = snapshot(world.server)
    if collections is not None:
        assert set(snap.infos) == collections, sorted(snap.infos)
    if NAME in snap.infos:
        check_snapshot(snap, plan)
    # a restart repaired everything: no warning left, nothing corrupt
    run_verify(world.data, allow_warnings=False)
    return snap


def check_keys_and_continue(world: World, snap: Snapshot, *, key_hashes: bool = True) -> None:
    """Keys still parse and pass the server's checks, the client can insert and delete again."""
    assert NAME in snap.infos
    assert snap.infos[NAME].keys_uploaded
    if key_hashes:
        assert _dir_key_hashes(world.data) == world.cache.key_hashes
    col = world.col
    assert col is not None
    before = set(snap.live_ids())
    new = [b"z%02d" % i for i in range(3)]
    col.insert_vectors(_vectors(3, 9, 0), new)  # one block
    victims = sorted(before)[:2]
    assert col.delete(victims) == 2
    after = snapshot(world.server)
    assert set(after.live_ids()) == (before | set(new)) - set(victims)
    assert len(after.all_ids()) == len(set(after.all_ids()))
    run_verify(world.data, allow_warnings=False)


# ---- operations ----
# Each takes (world, plan) and runs against the started server. It records what the server
# acknowledged in `plan` as it goes, so a kill in the middle leaves exactly the client's knowledge.
def _two_cluster(n_a: int, n_b: int, seed: int) -> np.ndarray:
    return np.concatenate([_vectors(n_a, seed, 0), _vectors(n_b, seed + 1, 1)])


def op_insert(world: World, plan: Plan) -> None:
    """Two insert calls (the client sends one block per cluster it touches): the first one block,
    the second two."""
    assert world.col is not None
    for u in range(2):
        a = [b"i%d-a%d" % (u, i) for i in range(2)]
        b = [b"i%d-b%d" % (u, i) for i in range(2)] if u else []
        ins = Insert([a, b] if b else [a])
        plan.inserts.append(ins)
        world.col.insert_vectors(_two_cluster(2, len(b), 20 + u), a + b)
        ins.acked = True


def _stream_blocks(world: World) -> tuple[list, list[list[bytes]]]:
    """Four blocks of three ids each, as a bulk load sends them (built by hand)."""
    col = world.col
    assert col is not None
    blocks, groups = [], []
    for k in range(4):
        ids = [b"s%d-%d" % (k, i) for i in range(3)]
        groups.append(ids)
        blocks.append(
            col.pb.InsertBlock(
                api_version=1,
                collection=col.name,
                cluster_id=k % 2,
                ids=ids,
                columns=world.cache.columns,
            )
        )
    return blocks, groups


def op_insert_stream(world: World, plan: Plan) -> None:
    """One Insert stream of four blocks (an InsertHeader, then one message per column)."""
    col = world.col
    assert col is not None
    blocks, groups = _stream_blocks(world)
    ins = Insert(groups)
    plan.inserts.append(ins)
    col._insert(blocks)
    ins.acked = True


def op_delete(world: World, plan: Plan) -> None:
    assert world.col is not None
    base = sorted(world.cache.base_live)
    for k in range(4):
        ids = base[3 * k : 3 * k + 2]
        dl = Delete(ids)
        plan.deletes.append(dl)
        world.col.delete(ids)
        dl.acked = True


def _dead_blocks_setup(world: World) -> None:
    """Delete every vector of the blocks of the second insert, so compaction can drop them."""
    assert world.col is not None
    cs = [b"c%02d" % i for i in range(6)]
    world.col.delete(cs)
    world.live -= set(cs)


def op_replace(world: World, plan: Plan) -> None:
    """The client's repack: GetBlock, decrypt, merge, ReplaceBlock."""
    assert world.col is not None
    plan.live_stable = True
    report = world.col.repack(threshold=0.1)
    assert report.groups >= 1


def _repack_setup(world: World) -> None:
    assert world.col is not None
    victims = [b"b00", b"b01", b"b02", b"c00"]  # blocks of cluster 0 with deleted slots
    world.col.delete(victims)
    world.live -= set(victims)


def op_recluster(world: World, plan: Plan) -> None:
    plan.live_stable = True
    assert world.col is not None
    world.col.recluster(n_clusters=2)


def op_upload_keys(world: World, plan: Plan) -> None:
    assert world.col is not None
    for _ in range(3):
        world.col._upload_keys()


def op_blob_put(world: World, plan: Plan) -> None:
    assert world.col is not None
    for v in world.blob_values[b"blob0"][1:]:
        world.col.blobs.put(NAME, KIND_RECORD, b"blob0", v)
        world.blob_values[b"acked"] = [v]


def op_blob_delete(world: World, plan: Plan) -> None:
    assert world.col is not None
    for k in sorted(world.blob_values):
        if k.startswith(b"blobd"):
            world.col.blobs.delete(NAME, KIND_RECORD, [k])
            world.blob_values.setdefault(b"deleted", []).append(k)


def op_drop_create(world: World, plan: Plan) -> None:
    """Drop the collection, then create it again with other ids (the re-create of a migration)."""
    col = world.col
    assert col is not None
    col.retire()
    world.blob_values[b"dropped"] = [b"1"]
    x = _two_cluster(3, 3, 40)
    Collection.create(
        col.pb,
        col.stub,
        NAME,
        x,
        [b"n%02d" % i for i in range(6)],
        preset="fast",
        n_clusters=2,
        kit=world.cache.kit,
        openfhe_version=world.cache.kit.openfhe_version,
    )
    world.blob_values[b"recreated"] = [b"1"]


def op_drop(world: World, plan: Plan) -> None:
    assert world.col is not None
    world.col.retire()
    world.blob_values[b"dropped"] = [b"1"]


def op_create(world: World, plan: Plan) -> None:
    """Create a second collection with the real client flow (spec, keys, centers, blocks)."""
    col = world.col
    assert col is not None
    Collection.create(
        col.pb,
        col.stub,
        "second",
        _two_cluster(3, 3, 50),
        [b"m%02d" % i for i in range(6)],
        preset="fast",
        n_clusters=2,
        kit=world.cache.kit,
        openfhe_version=world.cache.kit.openfhe_version,
    )
    world.blob_values[b"created"] = [b"1"]


# ---- setup and check per operation ----
def _blob_put_setup(world: World) -> None:
    assert world.col is not None
    values = [os.urandom(BLOB_SIZE) for _ in range(4)]
    world.blob_values[b"blob0"] = [world.cache.base_blobs[b"blob0"], *values]
    world.blob_values[b"acked"] = [world.cache.base_blobs[b"blob0"]]


def _blob_delete_setup(world: World) -> None:
    assert world.col is not None
    for i in range(4):
        v = os.urandom(BLOB_SIZE)
        world.col.blobs.put(NAME, KIND_RECORD, b"blobd%d" % i, v)
        world.blob_values[b"blobd%d" % i] = [v]


def _noop(world: World) -> None:
    pass


def _check_blob_put(world: World) -> None:
    col = world.col
    assert col is not None
    got = col.blobs.get(NAME, KIND_RECORD, b"blob0")
    values = world.blob_values[b"blob0"]
    last_acked = world.blob_values[b"acked"][0]
    # the last acknowledged value or the next one (in flight); never an older or a mixed value
    nxt = values.index(last_acked) + 1
    allowed = [last_acked] + ([values[nxt]] if nxt < len(values) else [])
    assert got in allowed, "blob holds neither the last acknowledged nor the in-flight value"
    # the client can write it again
    new = os.urandom(BLOB_SIZE)
    col.blobs.put(NAME, KIND_RECORD, b"blob0", new)
    assert col.blobs.get(NAME, KIND_RECORD, b"blob0") == new


def _check_blob_delete(world: World) -> None:
    from ombra.errors import BlobStoreError

    col = world.col
    assert col is not None
    acked = set(world.blob_values.get(b"deleted", []))
    for k in (b"blobd%d" % i for i in range(4)):
        try:
            got = col.blobs.get(NAME, KIND_RECORD, k)
        except BlobStoreError:
            got = None
        if k in acked:
            assert got is None, "an acknowledged blob delete is not there"
        else:
            assert got in (None, world.blob_values[k][0]), "blob holds a wrong value"
    col.blobs.put(NAME, KIND_RECORD, b"blobd0", os.urandom(BLOB_SIZE))


@dataclass
class Op:
    run: Callable[[World, Plan], None]
    setup: Callable[[World], None] = _noop
    # what to check after the restart (default: the collection and its invariants)
    collections: Callable[[World, Snapshot], set[str] | None] | None = None
    extra_args: tuple[str, ...] = ()
    check: Callable[[World], None] | None = None
    # after a crash the data of the collection can be gone or partial: only structure is checked
    structural: bool = False


def _all_collections(world: World, snap: Snapshot) -> set[str] | None:
    return None


OPS: dict[str, Op] = {
    "insert": Op(op_insert),
    "insert_stream": Op(op_insert_stream),
    "delete": Op(op_delete),
    "replace": Op(op_replace, _repack_setup),
    "recluster": Op(op_recluster, _repack_setup),
    "upload_keys": Op(op_upload_keys),
    "blob_put": Op(op_blob_put, _blob_put_setup, check=_check_blob_put),
    "blob_delete": Op(op_blob_delete, _blob_delete_setup, check=_check_blob_delete),
    "drop_create": Op(op_drop_create, structural=True),
    "drop": Op(op_drop, structural=True),
    "create": Op(op_create, structural=True),
}
RANDOM_OPS = [
    "insert",
    "insert_stream",
    "delete",
    "compact",
    "replace",
    "recluster",
    "upload_keys",
    "blob_put",
    "blob_delete",
    "drop_create",
    "create",
]
COMPACT_ARGS = ("--compact-interval-ms", "100")


def op_compact(world: World, plan: Plan) -> None:
    """The server compacts by itself on a timer: wait until it has dropped the dead blocks."""
    plan.live_stable = True
    pb, pbg = load_stubs()
    deadline = time.monotonic() + 30
    with grpc.insecure_channel(world.server.address) as ch:
        stub = pbg.OmbraStub(ch)
        while time.monotonic() < deadline:
            lb = stub.ListBlocks(pb.ListBlocksRequest(api_version=1, collection=NAME), timeout=60)
            if not any(b.n_vectors and b.n_vectors == b.n_deleted for b in lb.blocks):
                return
            time.sleep(0.05)
    raise AssertionError("compaction did not drop the dead blocks")


OPS["compact"] = Op(op_compact, _dead_blocks_setup, extra_args=COMPACT_ARGS)

_durations: dict[str, float] = {}


def _run_op(world: World, op: Op, plan: Plan, errors: list[BaseException]) -> None:
    try:
        op.run(world, plan)
    except BaseException as e:  # noqa: BLE001 - the client sees the kill as an error
        errors.append(e)


def _prepare(world: World, op: Op, env: dict[str, str] | None) -> Plan:
    """Setup on a plain server, then the server of the case (armed, with its arguments)."""
    world.start()
    plan = Plan(base_live=set(world.live))
    op.setup(world)
    plan.base_live = set(world.live)
    world.server.kill()
    world.start(env=env, args=list(op.extra_args))
    return plan


def _finish(world: World, op: Op, plan: Plan) -> None:
    """After the kill: restart, verify, check the invariants, continue working."""
    if op.structural:
        run_verify(world.data)
        world.restart_plain()
        snap = snapshot(world.server)
        run_verify(world.data, allow_warnings=False)
        # whatever state the collections are in, the client can start over from it
        assert world.col is not None
        for name in sorted(snap.infos):
            if name in ("crash", "second"):
                world.make_collection(world.col.pb, world.col.stub, name).retire()
        assert snapshot(world.server).infos == {}
        _recreate_and_use(world)
        return
    snap = check_after_restart(world, plan)
    assert NAME in snap.infos, "the collection disappeared"
    if op.check is not None:
        op.check(world)
    check_keys_and_continue(world, snap)


def _recreate_and_use(world: World) -> None:
    col = world.col
    assert col is not None
    new = Collection.create(
        col.pb,
        col.stub,
        NAME,
        _two_cluster(3, 3, 60),
        [b"r%02d" % i for i in range(6)],
        preset="fast",
        n_clusters=2,
        kit=world.cache.kit,
        openfhe_version=world.cache.kit.openfhe_version,
    )
    new.insert_vectors(_two_cluster(1, 1, 61), [b"r10", b"r11"])
    assert new.delete([b"r00"]) == 1
    snap = snapshot(world.server)
    assert sorted(snap.live_ids()) == sorted([b"r%02d" % i for i in range(1, 6)] + [b"r10", b"r11"])
    run_verify(world.data, allow_warnings=False)


def _recluster_leftovers(world: World) -> None:
    """A re-clustering killed half way can leave `crash.g1`: the old generation must be intact
    and the leftover must be droppable (that is what the client does before it tries again)."""
    snap = snapshot(world.server)
    assert set(snap.infos) <= {NAME, f"{NAME}.g1"}
    if f"{NAME}.g1" in snap.infos:
        assert world.col is not None
        world.make_collection(world.col.pb, world.col.stub, NAME, 1).retire()
        assert set(snapshot(world.server).infos) == {NAME}


OPS["recluster"].check = _recluster_leftovers


def _calibrate(cache: Cache, name: str) -> float:
    """Seconds the operation takes without a kill (measured once per session). Also proves that
    the operation and its invariants hold when nothing goes wrong."""
    if name not in _durations:
        op = OPS[name]
        world = World(cache, f"calib-{name}")
        try:
            plan = _prepare(world, op, None)
            errors: list[BaseException] = []
            t0 = time.monotonic()
            _run_op(world, op, plan, errors)
            _durations[name] = time.monotonic() - t0
            assert not errors, f"{name} failed without any kill: {errors[0]!r}"
            for ins in plan.inserts:
                assert ins.acked
            for dl in plan.deletes:
                assert dl.acked
            _finish(world, op, plan)
        finally:
            world.close()
    return _durations[name]


# ---- the tests ----
def _random_cases() -> list[pytest.param]:
    out = []
    for i in range(ITERS):
        op = RANDOM_OPS[i % len(RANDOM_OPS)]
        seed = BASE_SEED + i
        out.append(pytest.param(op, seed, id=f"{op}-s{seed}"))
    return out


@pytest.mark.parametrize(("op_name", "seed"), _random_cases())
def test_random_kill(cache: Cache, op_name: str, seed: int) -> None:
    """SIGKILL at a random time during the operation (the seed fixes the time)."""
    duration = _calibrate(cache, op_name)
    delay = random.Random(seed).uniform(0.0, duration * 1.1)  # noqa: S311 - not crypto
    op = OPS[op_name]
    world = World(cache, f"{op_name}-{seed}")
    try:
        plan = _prepare(world, op, None)
        errors: list[BaseException] = []
        thread = threading.Thread(target=_run_op, args=(world, op, plan, errors), daemon=True)
        thread.start()
        time.sleep(delay)
        world.server.kill()
        thread.join(timeout=120)
        assert not thread.is_alive()
        try:
            _finish(world, op, plan)
        except BaseException as e:
            e.add_note(f"seed={seed} op={op_name} kill after {delay:.3f} s of {duration:.3f} s")
            raise
    finally:
        world.close()


def _point_cases() -> list[pytest.param]:
    return [pytest.param(op, point, id=point) for op, points in POINTS.items() for point in points]


_POINT_OPS = {
    "insert": OPS["insert_stream"],
    "delete": OPS["delete"],
    "drop": OPS["drop"],
    "replace": OPS["replace"],
    "compact": OPS["compact"],
    "blob_put": OPS["blob_put"],
    "blob_delete": OPS["blob_delete"],
}


@pytest.mark.parametrize(("kind", "point"), _point_cases())
def test_crash_point(cache: Cache, kind: str, point: str) -> None:
    """The server stops itself (exit code 42) at one deterministic point; recovery must hold."""
    op = _POINT_OPS[kind]
    world = World(cache, f"point-{point}")
    try:
        plan = _prepare(world, op, {"OMBRA_CRASH_POINT": point})
        errors: list[BaseException] = []
        thread = threading.Thread(target=_run_op, args=(world, op, plan, errors), daemon=True)
        thread.start()
        code = None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            code = world.server._proc.poll()  # type: ignore[union-attr]
            if code is not None:
                break
            time.sleep(0.02)
        thread.join(timeout=120)
        assert code == CRASH_EXIT, (
            f"{point}: expected the server to stop with {CRASH_EXIT}, got {code}; the operation "
            f"finished without reaching the point? errors={errors!r}"
        )
        world.server.kill()
        _finish(world, op, plan)
    finally:
        world.close()


@pytest.mark.parametrize(
    "point",
    [
        "InsertBeforeSegmentRename",
        "InsertAfterUnlockedWrite",
        "InsertAfterIntent",
        "InsertAfterCatalog",
        "InsertAfterCommit",
    ],
)
def test_retry_after_crash(cache: Cache, point: str) -> None:
    """The client saw an error and sends the same stream again (idempotent insert): it succeeds,
    every id is stored exactly once, and a second retry changes nothing."""
    op = _POINT_OPS["insert"]
    world = World(cache, f"retry-{point}")
    try:
        plan = _prepare(world, op, {"OMBRA_CRASH_POINT": point})
        errors: list[BaseException] = []
        thread = threading.Thread(target=_run_op, args=(world, op, plan, errors), daemon=True)
        thread.start()
        deadline = time.monotonic() + 120
        code = None
        while time.monotonic() < deadline and code is None:
            code = world.server._proc.poll()  # type: ignore[union-attr]
            time.sleep(0.02)
        thread.join(timeout=120)
        assert code == CRASH_EXIT
        world.server.kill()
        run_verify(world.data)
        world.restart_plain()
        assert world.col is not None
        blocks, groups = _stream_blocks(world)
        for _ in range(2):  # the second one is a retry of a stored stream
            world.col._insert(blocks)
            snap = snapshot(world.server)
            ids = snap.all_ids(NAME)
            assert len(ids) == len(set(ids)), "an id is stored twice"
            assert {i for g in groups for i in g} <= set(snap.live_ids(NAME))
            assert set(snap.live_ids(NAME)) == plan.base_live | {i for g in groups for i in g}
        run_verify(world.data, allow_warnings=False)
    finally:
        world.close()


def test_points_table_matches_header() -> None:
    import re

    header = (
        Path(__file__).resolve().parents[1] / "server/storage/include/ombra/storage/failpoint.hpp"
    ).read_text()
    enum = re.search(r"enum class CrashPoint : int \{(.*?)\};", header, re.S)
    assert enum
    names = set(re.findall(r"^\s+([A-Z]\w+),", enum.group(1), re.M)) - {"None"}
    assert names == {p for pts in POINTS.values() for p in pts}
