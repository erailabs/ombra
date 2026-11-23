# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Decoy searches with fixed decoy groups, against the fake server of test_search_rounds."""

import random

import numpy as np
import pytest
from ombra import search
from ombra.decoy_groups import GroupInfo, build_groups
from ombra.probe import decoy_round_sizes
from test_search_rounds import C, _fake, _run

Q = np.array([1.0, 0, 0, 0])


@pytest.fixture(autouse=True)
def _no_rerank(monkeypatch):
    monkeypatch.setattr(search, "_rerank", lambda col, q, top, k: [])


def _groups(g, key=b"k" * 32, method="random", centers=None):
    salt = b"s" * 16
    return GroupInfo(g, method, salt, build_groups(key, salt, C, g, method, centers))


def _run_g(g, flt, per_cluster=5, **kw):
    requests: list[list[int]] = []
    col = _fake("decoy", per_cluster, requests, groups=_groups(g), **kw)
    res = search.run_search(col, Q, 3, filter=flt, level="decoy", _rng=random.Random(1))  # noqa: S311
    return res, requests, col.stats.groups.group_of


@pytest.mark.parametrize("g", [2, 3, 4, 6])
def test_grouped_decoy_does_two_rounds_of_whole_groups_without_repeats(g):
    for flt, per in ((None, 5), ({0: 1}, 5), ({0: 1}, 1)):
        res, req, grp = _run_g(g, flt, per)
        assert res.rounds == 2 and len(req) == 2
        flat = [c for r in req for c in r]
        assert len(flat) == len(set(flat))  # no cluster twice on the stream
        for r in req:  # every requested group is complete
            assert len(r) % g == 0
            for c in r:
                assert {int(x) for x in np.nonzero(grp == grp[c])[0]} <= set(r)
        assert set(res.probed) <= set(flat)


@pytest.mark.parametrize("g", [2, 3, 4, 6])
def test_grouped_round_sizes_do_not_depend_on_filter_or_query(g):
    want = decoy_round_sizes(6, C, g)
    shapes = set()
    for flt, per in ((None, 5), ({0: 1}, 5), ({0: 1}, 1), ({0: [0, 1]}, 10)):
        _, req, _ = _run_g(g, flt, per)
        shapes.add(tuple(len(r) for r in req))
    for seed in range(5):  # other queries: other center scores
        cs = np.random.default_rng(100 + seed).uniform(-1, 1, C)
        requests: list[list[int]] = []
        col = _fake("decoy", 5, requests, groups=_groups(g), cs=cs)
        search.run_search(col, Q, 3, level="decoy")
        shapes.add(tuple(len(r) for r in requests))
    assert shapes == {want}


def test_grouped_requests_repeat_byte_for_byte_whatever_the_rng():
    outs = []
    for seed in (1, 2, 3):
        requests: list[list[int]] = []
        col = _fake("decoy", 5, requests, groups=_groups(4))
        search.run_search(col, Q, 3, level="decoy", _rng=random.Random(seed))  # noqa: S311
        outs.append(requests)
    assert outs[0] == outs[1] == outs[2]
    requests = []  # and with the real SystemRandom
    col = _fake("decoy", 5, requests, groups=_groups(4))
    search.run_search(col, Q, 3, level="decoy")
    assert requests == outs[0]


def test_grouped_request_order_is_shuffled_with_real_clusters_not_always_first():
    firsts = []
    for i in range(30):
        cs = np.random.default_rng(i).uniform(-1, 1, C)
        requests: list[list[int]] = []
        col = _fake("decoy", 5, requests, groups=_groups(2), cs=cs)
        res = search.run_search(col, Q, 3, level="decoy")
        firsts.append(requests[0][0] in set(res.probed))
    assert any(firsts) and not all(firsts)


def test_grouped_rare_filter_full_scan_is_unchanged():
    res, req, _ = _run_g(4, {0: 1}, 0)  # 0% expected matches: scan everything
    assert req == [list(range(18)), list(range(18, 36))]
    assert res.strict_fallback and res.rounds == 2


@pytest.mark.parametrize("level", ["fast", "strict"])
def test_groups_only_shape_decoy_requests(level):
    requests: list[list[int]] = []
    col = _fake(level, 5, requests, groups=_groups(4))
    plain: list[list[int]] = []
    ref = _fake(level, 5, plain)
    search.run_search(col, Q, 3, level=level)
    search.run_search(ref, Q, 3, level=level)
    assert requests == plain


def test_without_groups_decoy_keeps_todays_requests():
    _, req = _run("decoy", None, seed=4)
    assert [len(r) for r in req] == [12, 24]
    assert _run("decoy", None, seed=4)[1] == req  # same private rng: the old random padding
