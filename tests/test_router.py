# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The router with several nodes on one machine .

Nodes are `tests/cpu_node.py` processes (they search on the CPU through local mode's LocalServer);
the router is the C++ `ombra_router`. One collection is built once through the router (client side
encryption is slow); the tests then use the raw gRPC stub, so they can compare the exact bytes.
Synthetic data only. Moving a cluster and GPU nodes are not covered here.
"""

from __future__ import annotations

import queue

import pytest

np = pytest.importorskip("numpy")
grpc = pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")
pytest.importorskip("openfhe")
pytest.importorskip("ombra._native", reason="the compiled extension is not built")

from server_harness import (  # noqa: E402
    CpuNodeProcess,
    RouterProcess,
    find_router_binary,
)

pytestmark = pytest.mark.openfhe

if find_router_binary() is None:
    pytest.skip("ombra_router (CPU build) not found: run `make build-cpu`", allow_module_level=True)

from ombra._proto import insert_requests, load_stubs  # noqa: E402
from ombra.search import filter_query  # noqa: E402

pb, pbg = load_stubs()
D = 384
N = 36
CLUSTERS = 6
NODE_KEY = "node-secret-token"
UNAVAILABLE = 19


def _unit(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def spec(name, clusters=CLUSTERS, tag="tag"):
    return pb.CollectionSpec(
        api_version=1,
        name=name,
        preset="m0_compact_n8192",
        dimension=D,
        security_level=pb.SECURITY_LEVEL_FAST,
        key_tag=tag,
        cluster_count=clusters,
    )


class Cluster:
    """Three nodes behind a router."""

    def __init__(self, root, n=3):
        self.root = root
        self.nodes = [
            CpuNodeProcess(root / f"node{i}", token=NODE_KEY, search_log=True) for i in range(n)
        ]
        for node in self.nodes:
            node.start()
        self.router = RouterProcess(root / "router", self.nodes, node_token=NODE_KEY)
        self.router.start()
        self.channel = None
        self.connect()

    def connect(self):
        if self.channel is not None:
            self.channel.close()
        self.channel = grpc.insecure_channel(self.router.address)
        self.stub = pbg.OmbraStub(self.channel)

    def node_stub(self, i):
        ch = grpc.insecure_channel(self.nodes[i].address)
        return ch, pbg.OmbraStub(ch)

    def md(self):
        return (("authorization", f"Bearer {NODE_KEY}"),)

    def close(self):
        if self.channel is not None:
            self.channel.close()
        self.router.kill()
        for n in self.nodes:
            n.kill()


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    c = Cluster(tmp_path_factory.mktemp("router"))
    yield c
    c.close()


# ---- collections without data: no encryption needed ----
def test_nodes_require_the_node_key(cluster):
    ch, stub = cluster.node_stub(0)
    try:
        with pytest.raises(grpc.RpcError) as ei:
            stub.ListCollections(pb.ListCollectionsRequest(), timeout=10)
        assert ei.value.code() == grpc.StatusCode.UNAUTHENTICATED
        stub.ListCollections(pb.ListCollectionsRequest(), metadata=cluster.md(), timeout=10)
    finally:
        ch.close()


def test_collection_calls_fan_out(cluster):
    s = cluster.stub
    r = s.CreateCollection(pb.CreateCollectionRequest(spec=spec("e1")), timeout=30)
    assert not r.HasField("error") and r.spec.name == "e1"
    for i in range(3):  # on every node
        ch, st = cluster.node_stub(i)
        assert not st.GetCollection(
            pb.GetCollectionRequest(name="e1"), metadata=cluster.md(), timeout=10
        ).HasField("error")
        ch.close()
    # every node has it: a real conflict
    r = s.CreateCollection(pb.CreateCollectionRequest(spec=spec("e1")), timeout=30)
    assert r.error.code == pb.ERROR_COLLECTION_EXISTS
    info = s.GetCollection(pb.GetCollectionRequest(name="e1"), timeout=30).info
    assert info.spec.name == "e1" and info.vector_count == 0 and not info.keys_uploaded
    names = [
        c.spec.name for c in s.ListCollections(pb.ListCollectionsRequest(), timeout=30).collections
    ]
    assert "e1" in names and names.count("e1") == 1
    assert not s.DropCollection(pb.DropCollectionRequest(name="e1"), timeout=30).HasField("error")
    assert (
        s.GetCollection(pb.GetCollectionRequest(name="e1"), timeout=30).error.code
        == pb.ERROR_COLLECTION_NOT_FOUND
    )
    assert (
        s.DropCollection(pb.DropCollectionRequest(name="e1"), timeout=30).error.code
        == pb.ERROR_COLLECTION_NOT_FOUND
    )


def test_create_retry_after_a_partial_create(cluster):
    """One node has the collection (an earlier create was cut short): the retry finishes it."""
    ch, st = cluster.node_stub(1)
    assert not st.CreateCollection(
        pb.CreateCollectionRequest(spec=spec("e2")), metadata=cluster.md(), timeout=10
    ).HasField("error")
    ch.close()
    r = cluster.stub.CreateCollection(pb.CreateCollectionRequest(spec=spec("e2")), timeout=30)
    assert not r.HasField("error")
    # a different spec on the node that has it is a conflict
    r = cluster.stub.CreateCollection(
        pb.CreateCollectionRequest(spec=spec("e2", clusters=3)), timeout=30
    )
    assert r.error.code == pb.ERROR_COLLECTION_EXISTS
    cluster.stub.DropCollection(pb.DropCollectionRequest(name="e2"), timeout=30)


def retry(fn, tries=30):
    """A client retries on UNAVAILABLE; the router's channel to a restarted node needs a moment."""
    import time

    for i in range(tries):
        try:
            return fn()
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.UNAVAILABLE or i == tries - 1:
                raise
            time.sleep(0.5)


def test_create_with_a_node_down_is_unavailable_then_succeeds(cluster):
    cluster.nodes[2].kill()
    with pytest.raises(grpc.RpcError) as ei:
        cluster.stub.CreateCollection(pb.CreateCollectionRequest(spec=spec("e3")), timeout=30)
    assert ei.value.code() == grpc.StatusCode.UNAVAILABLE
    cluster.nodes[2].start()
    r = retry(
        lambda: cluster.stub.CreateCollection(
            pb.CreateCollectionRequest(spec=spec("e3")), timeout=30
        )
    )
    assert not r.HasField("error")  # nodes 0 and 1 had it: same spec, so the retry completes it
    cluster.stub.DropCollection(pb.DropCollectionRequest(name="e3"), timeout=30)


# ---- one encrypted collection through the router ----
class Data:
    pass


@pytest.fixture(scope="module")
def data(cluster, tmp_path_factory):
    import ombra

    root = tmp_path_factory.mktemp("client")
    rng = np.random.default_rng(11)
    x = _unit(rng.standard_normal((N, D)))
    meta = [{"f": i % 3} for i in range(N)]
    ids = [f"v{i}" for i in range(N)]
    client = ombra.Client(cluster.router.address, insecure=True)
    client.create_collection(
        "c",
        x,
        ids,
        key_file=root / "k.key",
        passphrase="pw",  # noqa: S106
        preset="fast",
        n_clusters=CLUSTERS,
        filter_fields={"f": 3},
        metadata=meta,
        scrypt_params=(2**4, 8, 1),
    )
    d = Data()
    d.client, d.x, d.meta, d.ids = client, x, meta, ids
    d.col = client._core._get("c")[0]
    d.owners = {}
    for b in list_blocks(cluster.stub, "c"):
        if b.cluster_id < CLUSTERS:
            d.owners[b.cluster_id] = b.segment.split("/")[0]
    yield d
    client.close()


def list_blocks(stub, name, md=()):
    r = stub.ListBlocks(
        pb.ListBlocksRequest(api_version=1, collection=name), metadata=md, timeout=30
    )
    assert not r.HasField("error"), r.error
    return list(r.blocks)


def test_clusters_are_placed_on_one_node_each(cluster, data):
    routed = list_blocks(cluster.stub, "c")
    by_cluster = {}
    for b in routed:
        node, _, seg = b.segment.partition("/")
        assert node in ("n0", "n1", "n2") and seg
        by_cluster.setdefault(b.cluster_id, set()).add(node)
    assert set(by_cluster[CLUSTERS]) == {"n0", "n1", "n2"}  # the center blocks are on every node
    owners = {}
    for cl in range(CLUSTERS):
        assert len(by_cluster[cl]) == 1, "a data cluster lives on exactly one node"
        owners[cl] = next(iter(by_cluster[cl]))
    assert len(set(owners.values())) >= 2, "the clusters are spread over the nodes"
    assert data.owners == owners
    # what the nodes hold directly is the same
    for i in range(3):
        ch, st = cluster.node_stub(i)
        mine = {b.cluster_id for b in list_blocks(st, "c", cluster.md()) if b.cluster_id < CLUSTERS}
        ch.close()
        assert mine == {c for c, o in owners.items() if o == f"n{i}"}
    info = cluster.stub.GetCollection(pb.GetCollectionRequest(name="c"), timeout=30).info
    assert info.vector_count == N and info.keys_uploaded


def make_requests(data):
    q = _unit(np.random.default_rng(5).standard_normal(D))
    ext = filter_query(q, data.col.cards, {0: 1})
    start = pb.SearchStart(
        collection="c",
        key_tag=data.col.kit.key_tag,
        query=data.col.kit.encrypt_query(ext),
        filter_leaves=sum(data.col.cards),
    )
    order = [3, 0, 5, 1, 4, 2]
    return [
        pb.SearchRequest(start=start),
        pb.SearchRequest(chosen=pb.SearchChosen(cluster_ids=order[:3])),
        pb.SearchRequest(chosen=pb.SearchChosen(cluster_ids=order[3:])),
    ]


def run_stream(stub, requests, md=()):
    out = list(stub.Search(iter(requests), metadata=md, timeout=900))
    return out


def stable(resp):
    """The bytes of an answer without `last`, which the router sets itself."""
    r = pb.SearchResponse()
    r.CopyFrom(resp)
    if r.HasField("scores"):
        r.scores.last = False
    return r.SerializeToString()


def test_routed_search_passes_scores_through_unchanged_and_nodes_see_only_theirs(cluster, data):
    reqs = make_requests(data)
    for n in cluster.nodes:
        if n.search_log and n.search_log.exists():
            n.search_log.unlink()
    routed = run_stream(cluster.stub, reqs)
    data.requests, data.baseline = reqs, routed
    assert routed[0].HasField("centers")
    order = [3, 0, 5, 1, 4, 2]
    answers = routed[1:]
    assert [a.scores.cluster_id for a in answers] == order  # in the order of the request
    assert [a.scores.last for a in answers] == [False, False, True, False, False, True]
    for a in answers:
        assert len(a.scores.score_blocks) == len(a.scores.block_sizes) >= 1
        assert len(a.scores.ids) == sum(a.scores.block_sizes)
    # T3: a node was asked only for the clusters it owns, each cluster once overall
    asked = []
    for i, n in enumerate(cluster.nodes):
        mine = [c for ln in n.searched_clusters() for c in ln]
        assert all(data.owners[c] == f"n{i}" for c in mine), "a node saw a cluster it does not own"
        asked += mine
    assert sorted(asked) == sorted(order)
    # T2: every answer is byte-identical to the owning node's own answer
    direct = {}
    for i in range(3):
        mine = [c for c in order if data.owners[c] == f"n{i}"]
        ch, st = cluster.node_stub(i)
        sub = [reqs[0]]
        if mine:
            sub.append(pb.SearchRequest(chosen=pb.SearchChosen(cluster_ids=mine)))
        got = run_stream(st, sub, cluster.md())
        ch.close()
        if i == 0:
            assert stable(got[0]) == stable(routed[0])  # centers: the first node's, unchanged
        for g in got[1:]:
            direct[g.scores.cluster_id] = stable(g)
    for a in answers:
        assert stable(a) == direct[a.scores.cluster_id]


def test_client_search_levels_and_filters_match_brute_force(cluster, data):
    c, x = data.client, data.x
    q = x[7]
    # Strict scans every cluster: the exact nearest neighbours of the whole collection.
    want = sorted(range(N), key=lambda i: -float(x[i] @ q))[:3]
    hits = c.search("c", q, k=3, level="strict")
    assert [h.id for h in hits] == [f"v{i}" for i in want]
    # filtered, strict: only vectors with f == 1
    ok = [i for i in range(N) if data.meta[i]["f"] == 1]
    want_f = sorted(ok, key=lambda i: -float(x[i] @ q))[:3]
    hits = c.search("c", q, k=3, filter={"f": 1}, level="strict")
    assert [h.id for h in hits] == [f"v{i}" for i in want_f]
    # decoy and fast, probing every cluster, give the same exact answer
    for level in ("decoy", "fast"):
        hits = c.search("c", q, k=3, level=level, n_probe=CLUSTERS)
        assert [h.id for h in hits] == [f"v{i}" for i in want]


# ---- failures ----
def test_node_killed_between_rounds_gives_retryable_error_and_identical_replay(cluster, data):
    reqs = data.requests
    victim = next(i for i in range(3) if any(o == f"n{i}" for o in data.owners.values()))
    q: queue.Queue = queue.Queue()

    def gen():
        while True:
            item = q.get()
            if item is None:
                return
            yield item

    q.put(reqs[0])
    call = cluster.stub.Search(gen(), timeout=900)
    first = next(call)
    assert first.HasField("centers")
    cluster.nodes[victim].kill()  # between the center scores and the first round
    q.put(reqs[1])
    q.put(reqs[2])
    rest = []
    try:
        for r in call:
            rest.append(r)
            if r.HasField("error"):
                break
    finally:
        q.put(None)
    assert rest and rest[-1].HasField("error") and rest[-1].error.code == UNAVAILABLE
    cluster.nodes[victim].start()
    again = run_stream(cluster.stub, reqs)
    assert [stable(r) for r in again] == [stable(r) for r in data.baseline]
    assert [r.scores.last for r in again[1:]] == [r.scores.last for r in data.baseline[1:]]


def test_router_killed_midstream_replays_to_a_new_router(cluster, data):
    reqs = data.requests
    q: queue.Queue = queue.Queue()

    def gen():
        while True:
            item = q.get()
            if item is None:
                return
            yield item

    q.put(reqs[0])
    call = cluster.stub.Search(gen(), timeout=900)
    assert next(call).HasField("centers")
    cluster.router.kill()
    with pytest.raises(grpc.RpcError) as ei:
        q.put(reqs[1])
        list(call)
    assert ei.value.code() == grpc.StatusCode.UNAVAILABLE  # the client retries on this
    q.put(None)
    cluster.router.start()
    cluster.connect()
    again = run_stream(cluster.stub, reqs)  # the same requests, the same decoys
    assert [stable(r) for r in again] == [stable(r) for r in data.baseline]


def key_bytes(cluster, name="c"):
    d = cluster.nodes[0].data_dir / "c" / name.encode().hex() / "keys"
    return {k: (d / k).read_bytes() for k in ("context", "evalmult", "automorphism")}


def key_chunks(name, keys):
    kinds = {"context": 1, "evalmult": 2, "automorphism": 3}
    for kind, blob in keys.items():
        parts = [blob[i : i + (1 << 20)] for i in range(0, len(blob), 1 << 20)] or [b""]
        off = 0
        for j, p in enumerate(parts):
            yield pb.UploadKeysChunk(
                api_version=1,
                collection=name if (kind == "context" and j == 0) else "",
                kind=kinds[kind],
                offset=off,
                data=p,
                last=j == len(parts) - 1,
            )
            off += len(p)


def test_upload_keys_with_a_node_down_fails_and_the_retry_succeeds(cluster, data):
    keys = key_bytes(cluster)
    s = cluster.stub
    assert not s.CreateCollection(
        pb.CreateCollectionRequest(spec=cluster_spec_of(cluster, "c", "k1")), timeout=30
    ).HasField("error")
    cluster.nodes[1].kill()
    with pytest.raises(grpc.RpcError) as ei:
        s.UploadKeys(key_chunks("k1", keys), timeout=120)
    assert ei.value.code() == grpc.StatusCode.UNAVAILABLE
    cluster.nodes[1].start()
    assert not retry(lambda: s.UploadKeys(key_chunks("k1", keys), timeout=120)).HasField("error")
    for i in range(3):
        d = cluster.nodes[i].data_dir / "c" / b"k1".hex() / "keys"
        assert {k: (d / k).read_bytes() for k in keys} == keys
    assert s.GetCollection(pb.GetCollectionRequest(name="k1"), timeout=30).info.keys_uploaded
    s.DropCollection(pb.DropCollectionRequest(name="k1"), timeout=30)


def cluster_spec_of(cluster, src, name):
    ch, st = cluster.node_stub(0)
    try:
        s = pb.CollectionSpec()
        s.CopyFrom(
            st.GetCollection(pb.GetCollectionRequest(name=src), metadata=cluster.md()).info.spec
        )
    finally:
        ch.close()
    s.name = name
    return s


def fetch_block(stub, name, segment):
    got = list(
        stub.GetBlock(
            pb.GetBlockRequest(api_version=1, collection=name, segment=segment), timeout=60
        )
    )
    assert got[0].HasField("header"), got[0]
    h = got[0].header
    cols = [g.column for g in got[1:]]
    assert len(cols) == h.n_columns
    return h, cols


def test_insert_delete_and_replace_block_through_the_router(cluster, data):
    s = cluster.stub
    blocks = []
    seen_center = False
    for b in list_blocks(s, "c"):
        center = b.cluster_id == CLUSTERS
        if center and seen_center:
            continue
        seen_center = seen_center or center
        h, cols = fetch_block(s, "c", b.segment)
        blocks.append(
            pb.InsertBlock(
                api_version=1,
                collection="c2",
                cluster_id=b.cluster_id,
                ids=list(h.ids),
                columns=cols,
                is_center=center,
            )
        )
    total = sum(len(b.ids) for b in blocks)
    assert not s.CreateCollection(
        pb.CreateCollectionRequest(spec=cluster_spec_of(cluster, "c", "c2")), timeout=30
    ).HasField("error")
    assert not s.UploadKeys(key_chunks("c2", key_bytes(cluster)), timeout=120).HasField("error")

    # a node goes down in the middle of the insert: the call fails, nothing is half-acknowledged
    victim = 1
    cluster.nodes[victim].kill()
    with pytest.raises(grpc.RpcError) as ei:
        s.Insert(insert_requests(pb, blocks), timeout=900)
    assert ei.value.code() == grpc.StatusCode.UNAVAILABLE
    cluster.nodes[victim].start()
    # the retry sends the same bytes: blocks that were stored are accepted again, none twice
    r = retry(lambda: s.Insert(insert_requests(pb, blocks), timeout=900))
    assert not r.HasField("error") and r.inserted == total
    again = s.Insert(insert_requests(pb, blocks), timeout=900)  # a repeat of a finished insert
    assert not again.HasField("error") and again.inserted == total
    assert s.GetCollection(pb.GetCollectionRequest(name="c2"), timeout=30).info.vector_count == N
    b2 = list_blocks(s, "c2")
    assert len([b for b in b2 if b.cluster_id < CLUSTERS]) == len(
        [b for b in list_blocks(s, "c") if b.cluster_id < CLUSTERS]
    )
    owners2 = {}
    for b in b2:
        if b.cluster_id < CLUSTERS:
            owners2[b.cluster_id] = b.segment.split("/")[0]
    assert owners2 == data.owners  # placement depends on the cluster id only

    # delete goes to every node; unknown ids are ignored
    gone = [b"v0", b"v1", b"does-not-exist"]
    d = s.Delete(pb.DeleteRequest(api_version=1, collection="c2", ids=gone), timeout=60)
    assert not d.HasField("error") and d.deleted == 2
    assert s.GetCollection(pb.GetCollectionRequest(name="c2"), timeout=30).info.deleted_count == 2

    # ReplaceBlock: segment names carry the node; the new name does too
    old = next(b for b in list_blocks(s, "c") if b.cluster_id < CLUSTERS)
    h, cols = fetch_block(s, "c", old.segment)

    def replace():
        yield pb.ReplaceBlockRequest(
            api_version=1,
            header=pb.ReplaceBlockHeader(collection="c", segments=[old.segment], ids=list(h.ids)),
        )
        for col in cols:
            yield pb.ReplaceBlockRequest(api_version=1, column=col)

    r = s.ReplaceBlock(replace(), timeout=60)
    assert not r.HasField("error"), r.error
    assert r.segment.split("/")[0] == old.segment.split("/")[0]
    now = {b.segment for b in list_blocks(s, "c")}
    assert r.segment in now and old.segment not in now
    # blocks of two nodes cannot be merged
    segs = [b.segment for b in list_blocks(s, "c") if b.cluster_id < CLUSTERS]
    other = next((x for x in segs if x.split("/")[0] != r.segment.split("/")[0]), None)
    if other:
        bad = s.ReplaceBlock(
            iter(
                [
                    pb.ReplaceBlockRequest(
                        api_version=1,
                        header=pb.ReplaceBlockHeader(
                            collection="c", segments=[r.segment, other], ids=[]
                        ),
                    )
                ]
            ),
            timeout=60,
        )
        assert bad.error.code == pb.ERROR_BLOCK_MISMATCH
    s.DropCollection(pb.DropCollectionRequest(name="c2"), timeout=60)


def test_blobs_are_placed_by_the_hash_of_the_id(cluster, data):
    s = cluster.stub
    size = 16384
    placed = {}
    for i in range(12):
        bid = f"blob-{i}".encode()
        body = bytes([i]) * size

        def put(bid=bid, body=body):
            yield pb.PutBlobRequest(
                api_version=1,
                header=pb.PutBlobHeader(
                    collection="c", kind=pb.BLOB_KIND_DOCUMENT, id=bid, size=size
                ),
            )
            yield pb.PutBlobRequest(api_version=1, data=body)

        assert not s.PutBlob(put(), timeout=30).HasField("error")
        got = b"".join(
            m.data
            for m in s.GetBlob(
                pb.GetBlobRequest(
                    api_version=1, collection="c", kind=pb.BLOB_KIND_DOCUMENT, id=bid
                ),
                timeout=30,
            )
            if m.WhichOneof("part") == "data"
        )
        assert got == body
        for n in range(3):
            ch, st = cluster.node_stub(n)
            try:
                found = [
                    m
                    for m in st.GetBlob(
                        pb.GetBlobRequest(
                            api_version=1, collection="c", kind=pb.BLOB_KIND_DOCUMENT, id=bid
                        ),
                        metadata=cluster.md(),
                        timeout=30,
                    )
                    if m.WhichOneof("part") == "size"
                ]
            finally:
                ch.close()
            if found:
                placed.setdefault(bid, []).append(n)
    assert all(len(v) == 1 for v in placed.values()) and len(placed) == 12
    assert len({v[0] for v in placed.values()}) >= 2
    d = s.DeleteBlobs(
        pb.DeleteBlobsRequest(
            api_version=1, collection="c", kind=pb.BLOB_KIND_DOCUMENT, ids=list(placed)
        ),
        timeout=30,
    )
    assert d.deleted == 12
