# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The leakage harness (core/bench/leakage.py) on a fake stub: calls, streams, shape, id search and
the intersection attack. CPU only, synthetic data."""

from __future__ import annotations

import importlib.util
import queue
import sys
from pathlib import Path

import pytest

pytest.importorskip("grpc_tools")
pytest.importorskip("google.protobuf")

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "leakage", REPO_ROOT / "core" / "bench" / "leakage.py"
)
lk = importlib.util.module_from_spec(_spec)
sys.modules["leakage"] = lk  # dataclasses need the module registered
_spec.loader.exec_module(lk)

from ombra._proto import load_stubs  # noqa: E402
from ombra.retry import Replayable  # noqa: E402

pb = load_stubs(grpc_module=False)[0]
ID = b"synthetic-record-00000042"


class FakeStub:
    """Method names of the Ombra stub; calls are logged so tests can check what ran when."""

    def __init__(self):
        self.log = []
        self.retry_policy = "p"  # a non-RPC attribute that must pass through

    def DeleteBlobs(self, req, timeout=None):  # noqa: N802
        self.log.append("DeleteBlobs")
        return pb.DeleteBlobsResponse(deleted=len(req.ids))

    def Delete(self, req):  # noqa: N802
        return pb.DeleteResponse(deleted=len(req.ids))

    def PutBlob(self, it):  # noqa: N802
        self.log.append("PutBlob-start")
        for _ in it:
            pass
        self.log.append("PutBlob-end")
        return pb.PutBlobResponse(api_version=1)

    def GetBlob(self, req):  # noqa: N802
        self.log.append("GetBlob")
        yield pb.GetBlobResponse(api_version=1, size=5)
        yield pb.GetBlobResponse(api_version=1, data=b"hello")

    def Search(self, it):  # noqa: N802
        self.log.append("Search")
        for req in it:
            if req.WhichOneof("step") == "start":
                yield pb.SearchResponse(centers=pb.SearchCenterScores(center_count=4))
            else:
                for c in req.chosen.cluster_ids:
                    yield pb.SearchResponse(scores=pb.SearchScores(cluster_id=c, last=True))

    def cancel_all(self):
        return "cancelled"


def _stub():
    sink = lk._Sink(True)
    inner = FakeStub()
    return lk.RecordingStub(inner, sink), inner, sink


def test_unary_call_is_recorded_and_passthrough_works():
    stub, inner, sink = _stub()
    req = pb.DeleteBlobsRequest(api_version=1, collection="c", kind=1, ids=[ID, b"x" * 9])
    resp = stub.DeleteBlobs(req, timeout=5)
    assert resp.deleted == 2
    (call,) = sink.calls
    assert call.method == "DeleteBlobs"
    assert call.options == {"timeout": 5}  # options kept apart, not in the summary
    (m,) = call.requests
    assert m.size == req.ByteSize() and m.kind == "DeleteBlobsRequest"
    assert m.fields["ids"] == (("bytes", len(ID)), ("bytes", 9))
    assert m.fields["collection"] == "c"
    assert call.responses[0].size == resp.ByteSize()
    assert stub.retry_policy == "p" and stub.cancel_all() == "cancelled"


def test_client_stream_is_lazy_and_recorded():
    stub, inner, sink = _stub()
    pulled = []

    def gen():
        for i in range(3):
            pulled.append(i)
            yield pb.PutBlobRequest(api_version=1, data=b"d" * (i + 1))

    stub.PutBlob(gen())
    assert pulled == [0, 1, 2]
    (call,) = sink.calls
    assert [m.fields["data"] for m in call.requests] == [("bytes", 1), ("bytes", 2), ("bytes", 3)]
    assert inner.log == ["PutBlob-start", "PutBlob-end"]


def test_request_stream_is_not_consumed_before_the_transport_pulls():
    consumed = []

    class Inner:
        def PutBlob(self, it):  # noqa: N802
            assert consumed == []  # the wrapper did not read the generator on the way in
            for _ in it:
                pass
            return pb.PutBlobResponse()

    def gen():
        consumed.append(1)
        yield pb.PutBlobRequest(api_version=1, data=b"x")

    sink = lk._Sink(True)
    lk.RecordingStub(Inner(), sink).PutBlob(gen())
    assert consumed == [1] and len(sink.calls[0].requests) == 1


def test_replayable_stays_replayable_and_counts_attempts():
    sink = lk._Sink(True)
    seen = []

    class Inner:
        def PutBlob(self, arg):  # noqa: N802
            seen.append(arg)
            for _ in arg:
                pass
            for _ in arg:  # a retry iterates it again
                pass
            return pb.PutBlobResponse()

    stub = lk.RecordingStub(Inner(), sink)
    stub.PutBlob(Replayable(lambda: iter([pb.PutBlobRequest(data=b"ab")])))
    assert isinstance(seen[0], Replayable)
    (call,) = sink.calls
    assert call.attempts == 2 and len(call.requests) == 2


def test_server_stream_is_lazy_and_recorded():
    stub, inner, sink = _stub()
    out = stub.GetBlob(pb.GetBlobRequest(api_version=1, collection="c", id=ID))
    assert inner.log == []  # a generator has not started: the wrapper did not read it
    assert [type(r).__name__ for r in out] == ["GetBlobResponse"] * 2
    (call,) = sink.calls
    assert [m.size for m in call.responses] == [
        pb.GetBlobResponse(api_version=1, size=5).ByteSize(),
        pb.GetBlobResponse(api_version=1, data=b"hello").ByteSize(),
    ]
    assert call.requests[0].fields["id"] == ("bytes", len(ID))


def test_bidi_search_is_interactive_and_cluster_ids_are_extracted():
    stub, inner, sink = _stub()
    q: queue.Queue = queue.Queue()

    def requests():
        while True:
            item = q.get()
            if item is None:
                return
            yield item

    q.put(pb.SearchRequest(start=pb.SearchStart(collection="c", key_tag="t", query=b"q" * 50)))
    call_obj = stub.Search(requests())
    first = next(call_obj)  # interactive: the next request depends on this answer
    assert first.WhichOneof("step") == "centers"
    q.put(pb.SearchRequest(chosen=pb.SearchChosen(cluster_ids=[3, 1])))
    assert [next(call_obj).scores.cluster_id for _ in range(2)] == [3, 1]
    q.put(pb.SearchRequest(chosen=pb.SearchChosen(cluster_ids=[2])))
    assert next(call_obj).scores.cluster_id == 2
    q.put(None)
    assert list(call_obj) == []
    view = lk.View(sink.calls)
    assert view.requested_clusters() == [{1, 3}, {2}]
    assert lk.requested_clusters(view) == [{1, 3}, {2}]
    assert view.collections() == {"c"}
    assert len(sink.calls[0].responses) == 4


def test_shape_ignores_varying_fields_and_keeps_the_rest():
    def view(key_tag, clusters, timeout, query_len=50):
        stub, _, sink = _stub()
        it = iter(
            [
                pb.SearchRequest(
                    start=pb.SearchStart(collection="c", key_tag=key_tag, query=b"q" * query_len)
                ),
                pb.SearchRequest(chosen=pb.SearchChosen(cluster_ids=clusters)),
            ]
        )
        list(stub.Search(it))
        sink.calls[0].options["timeout"] = timeout
        return lk.View(sink.calls)

    a, b = view("tag-a", [1, 2], 10), view("tag-b", [3, 4], 99)
    assert a.shape() == b.shape()  # key tag, cluster ids and deadline are ignored
    assert hash(a.shape()) == hash(b.shape())
    assert a.shape(include_clusters=True) != b.shape(include_clusters=True)
    assert a.shape(drop=()) != b.shape(drop=())  # the key tag would tell them apart
    assert a.shape() != view("tag-a", [1, 2, 3], 10).shape()  # counts stay
    assert a.shape() != view("tag-a", [1, 2], 10, query_len=51).shape()  # sizes stay
    assert a.shape(include_timing=True)  # timing class is available, off by default


def test_timing_class_is_log2_millis():
    c = lk.Call("X", start=0.0, end=0.0)
    assert c.timing_class == 0
    c.end = 0.1  # 100 ms
    assert c.timing_class == 6  # floor(log2(101))
    c.end = 0.003
    assert c.timing_class == 2


def test_contains_any_finds_an_id_inside_any_bytes_field():
    stub, _, sink = _stub()
    blob = b"\x00" * 7 + ID + b"\xff" * 5
    stub.PutBlob(iter([pb.PutBlobRequest(api_version=1, data=blob)]))  # not a named id field
    stub.Delete(pb.DeleteRequest(api_version=1, collection="c", ids=[b"other-synthetic-id-1"]))
    view = lk.View(sink.calls)
    assert lk.contains_any(view, [ID])
    assert lk.contains_any(view, [ID.decode()])
    assert lk.contains_any(view, [b"other-synthetic-id-1"])
    assert not lk.contains_any(view, [b"synthetic-record-99999999"])
    with pytest.raises(ValueError):
        lk.contains_any(view, [b"v1"])  # too short to mean anything
    assert view.record_ids()["Delete"] == [b"other-synthetic-id-1"]


def test_id_helpers_and_missing_payload():
    stub, _, sink = _stub()
    hdr = pb.PutBlobHeader(collection="c", kind=1, id=ID, size=4)
    stub.PutBlob(iter([pb.PutBlobRequest(api_version=1, header=hdr)]))
    list(stub.GetBlob(pb.GetBlobRequest(api_version=1, collection="c", id=b"b" * 10)))
    stub.DeleteBlobs(pb.DeleteBlobsRequest(api_version=1, collection="c", ids=[b"d" * 10]))
    bi = lk.View(sink.calls).blob_ids()
    assert bi == {"GetBlob": [b"b" * 10], "PutBlob": [ID], "DeleteBlobs": [b"d" * 10]}
    s2 = lk._Sink(False)
    lk.RecordingStub(FakeStub(), s2).Delete(pb.DeleteRequest(collection="c", ids=[ID]))
    with pytest.raises(ValueError):
        lk.contains_any(lk.View(s2.calls), [ID])


def test_recording_stops_after_detach():
    stub, _, sink = _stub()
    sink.active = False
    stub.Delete(pb.DeleteRequest(collection="c", ids=[ID]))
    assert sink.calls == []


def test_intersection_attack_on_hand_made_sets():
    real = {1, 2}
    views = [[{1, 2, 5, 6}, {7}], [{1, 2, 8, 9}], [{1, 2, 5, 9}, {1}]]
    first = lk.intersection_attack(views, real)["first_round"]
    assert first == {"size": 2, "success": True, "precision": 1.0, "recall": 1.0}
    union = lk.intersection_attack(views, real)["union"]  # {1,2,5,6,7} & {1,2,8,9} & {1,2,5,9,1}
    assert union["size"] == 2 and union["success"]
    one = lk.intersection_attack(views[:1], real)["first_round"]
    assert one == {"size": 4, "success": False, "precision": 0.5, "recall": 1.0}
    empty = lk.intersection_attack([[{1}], [{2}]], real)["first_round"]
    assert empty == {"size": 0, "success": False, "precision": 0.0, "recall": 0.0}
    part = lk.intersection_attack([[{1, 9}], [{1, 8}]], real)["first_round"]
    assert part == {"size": 1, "success": True, "precision": 1.0, "recall": 0.5}
    with pytest.raises(ValueError):
        lk.intersection_attack([[]], real)


def test_attack_curve_averages_trials():
    real = {1, 2}
    t1 = [[{1, 2, 3}], [{1, 2, 4}], [{1, 2, 3}]]  # n=1: size 3; n=2: {1,2}
    t2 = [[{1, 9}], [{1, 9}], [{1, 2}]]  # n=1: {1,9}; n=2: {1,9}; n=3: {1}
    curve = lk.attack_curve([t1, t2], real, ns=(1, 2, 3))
    f1 = curve[1]["first_round"]
    assert f1["success"] == 0.0 and f1["size"] == (3 + 2) / 2
    assert f1["precision"] == (2 / 3 + 0.5) / 2 and f1["recall"] == (1.0 + 0.5) / 2
    f2 = curve[2]["first_round"]
    assert f2["success"] == 0.5 and f2["size"] == (2 + 2) / 2
    assert f2["precision"] == (1.0 + 0.5) / 2
    f3 = curve[3]["first_round"]
    assert f3["success"] == 1.0 and f3["size"] == (2 + 1) / 2
    assert curve[3]["union"]["success"] == 1.0
    per_trial = lk.attack_curve([t1, t2], [real, {1, 9}], ns=(2,))[2]["first_round"]
    assert per_trial["success"] == 1.0
    with pytest.raises(ValueError):
        lk.attack_curve([t1], real, ns=(4,))


def test_shape_include_ids_tells_apart_fetches_of_different_records():
    def view(blob_id):
        stub, _, sink = _stub()
        list(stub.GetBlob(pb.GetBlobRequest(api_version=1, collection="c", id=blob_id)))
        return lk.View(sink.calls)

    a, b = view(b"a" * 12), view(b"b" * 12)
    assert a.shape() == b.shape()  # same length, so the same shape
    assert a.shape(include_ids=True) != b.shape(include_ids=True)
    assert a.shape(include_ids=True) == view(b"a" * 12).shape(include_ids=True)
