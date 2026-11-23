# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Fixed decoy groups: keyed grouping, group requests, planner and the sealed stats blob."""

import numpy as np
import pytest
from ombra.blobstore import KIND_RECORD, InMemoryBlobStore
from ombra.decoy_groups import GroupInfo, GroupRequester, build_groups, members_of
from ombra.documents import DocumentCodec
from ombra.probe import STATS_ID, ClusterStats, ProbePlanner, decoy_round_sizes

KEY, SALT = b"k" * 32, b"s" * 16


def _centers(n=64, d=8, seed=0):
    x = np.random.default_rng(seed).normal(size=(n, d))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


@pytest.mark.parametrize("method", ["random", "neighbours"])
@pytest.mark.parametrize("g", [2, 4, 8])
def test_groups_partition_the_clusters_into_groups_of_g(method, g):
    c = _centers()
    grp = build_groups(KEY, SALT, len(c), g, method, c)
    assert sorted(np.bincount(grp)) == [g] * (len(c) // g)
    assert sorted(x for m in members_of(grp) for x in m) == list(range(len(c)))


@pytest.mark.parametrize("method", ["random", "neighbours"])
def test_grouping_is_keyed_and_repeatable(method):
    c = _centers()
    a = build_groups(KEY, SALT, len(c), 4, method, c)
    assert np.array_equal(a, build_groups(KEY, SALT, len(c), 4, method, c))
    assert not np.array_equal(a, build_groups(b"j" * 32, SALT, len(c), 4, method, c))
    assert not np.array_equal(a, build_groups(KEY, b"t" * 16, len(c), 4, method, c))


def test_neighbour_groups_are_closer_than_random_ones():
    c = _centers(128, 16)

    def spread(grp):
        return np.mean(
            [np.mean((c[m] - c[m].mean(axis=0)) ** 2) for m in members_of(grp) if len(m) > 1]
        )

    near = build_groups(KEY, SALT, len(c), 4, "neighbours", c)
    rnd = build_groups(KEY, SALT, len(c), 4, "random", c)
    assert spread(near) < 0.8 * spread(rnd)


def test_grouping_refuses_bad_input():
    with pytest.raises(ValueError):
        build_groups(KEY, SALT, 10, 4)  # not a multiple
    with pytest.raises(ValueError):
        build_groups(KEY, SALT, 8, 2, "bogus")
    with pytest.raises(ValueError):
        build_groups(KEY, SALT, 8, 2, "neighbours", None)
    with pytest.raises(ValueError):
        GroupInfo(2, "random", SALT, np.array([0, 0, 0, 1]))  # group sizes 3 and 1


def _requester(n=64, g=4):
    grp = build_groups(KEY, SALT, n, g)
    return GroupRequester(KEY, SALT, grp), grp


def test_a_request_is_whole_groups_with_exact_count_and_no_repeat():
    req, grp = _requester()
    real = [3, 17, 40]
    asked: set[int] = set()
    r1 = req.request(1, real, 6, asked)
    assert len(r1) == 24 and len(set(r1)) == 24
    assert len({int(grp[c]) for c in r1}) == 6
    for c in real:  # the whole group of every real cluster is in
        assert set(members_of(grp)[grp[c]]) <= set(r1)
    asked.update(r1)
    r2 = req.request(2, [50], 8, asked)  # cluster 50 may already be asked
    assert len(r2) == 32 and not set(r1) & set(r2)


def test_same_real_group_set_gives_identical_requests():
    grp = build_groups(KEY, SALT, 64, 4)
    mem = members_of(grp)
    a = GroupRequester(KEY, SALT, grp).request(1, [mem[2][0], mem[9][1]], 6, set())
    b = GroupRequester(KEY, SALT, grp).request(1, [mem[9][3], mem[2][2], mem[2][0]], 6, set())
    assert a == b  # any clusters of the same groups, any order: the same bytes
    c = GroupRequester(KEY, SALT, grp).request(1, [mem[2][0], mem[10][1]], 6, set())
    assert a != c
    d = GroupRequester(KEY, SALT, grp).request(2, [mem[2][0], mem[9][1]], 6, set())
    assert a != d  # the round number is part of the PRF input
    other = GroupRequester(b"j" * 32, SALT, grp).request(1, [mem[2][0], mem[9][1]], 6, set())
    assert a != other  # keyed


def test_request_order_is_keyed_not_real_first():
    grp = build_groups(KEY, SALT, 64, 4)
    mem = members_of(grp)
    firsts = []
    for gid in range(16):
        real = mem[gid]
        r = GroupRequester(KEY, SALT, grp).request(1, real, 4, set())
        firsts.append(r[0] in real)
    assert any(firsts) and not all(firsts)


def test_round_sizes_are_multiples_of_g_and_match_the_ungrouped_ones_at_g1():
    for first, n in ((6, 36), (6, 16), (10, 12), (16, 256)):
        assert decoy_round_sizes(first, n, 1) == decoy_round_sizes(first, n)
    assert decoy_round_sizes(16, 256, 4) == (32, 64)
    for g in (2, 4, 8):
        r1, r2 = decoy_round_sizes(16, 256, g)
        assert r1 % g == 0 and r2 % g == 0 and r1 + r2 <= 256
        assert 32 <= r1 < 32 + g and 64 <= r2 < 64 + g


def test_planner_returns_whole_groups_and_stays_inside_the_round_sizes():
    n, g = 64, 4
    grp = build_groups(KEY, SALT, n, g)
    info = GroupInfo(g, "random", SALT, grp)
    cs = np.random.default_rng(3).uniform(-1, 1, n)
    order = [int(i) for i in np.argsort(-cs, kind="stable")]
    r1, r2 = decoy_round_sizes(8, n, g)
    p = ProbePlanner(order, "plain2", 3, 8, cap1=16, groups=info, cap2=r2)
    a = p.first_round()
    assert len(a) % g == 0 and len(a) <= r1
    assert all(set(members_of(grp)[grp[c]]) <= set(a) for c in a)
    assert int(grp[a[0]]) == int(grp[order[0]])  # the best cluster's group comes first
    b = p.next_round(0, set(a))
    assert len(b) % g == 0 and len(b) <= r2 and not set(a) & set(b)
    assert p.next_round(0) == []


def test_planner_without_groups_is_unchanged():
    order = list(range(10))
    p = ProbePlanner(order, "plain2", 3, 2, cap1=4)
    assert p.first_round() == [0, 1] and p.next_round(0) == [2, 3, 4, 5]


def _stats(groups):
    s = ClusterStats.empty(8, (2,))
    s.sizes[:] = 5
    s.counts[0][:, 1] = 2
    s.counts[0][:, 0] = 3
    s.groups = groups
    return s


def test_stats_blob_round_trips_groups_as_v4_and_no_groups_as_v3():
    info = GroupInfo(2, "neighbours", SALT, build_groups(KEY, SALT, 8, 2))
    codec = DocumentCodec(KEY, "col")
    store = InMemoryBlobStore()
    for groups in (None, info):
        s = _stats(groups)
        want = 3 if groups is None else 4
        assert int.from_bytes(s.to_bytes()[4:6], "little") == want
        store.put("col", KIND_RECORD, STATS_ID, s.seal(codec))
        back = ClusterStats.unseal(codec, store.get("col", KIND_RECORD, STATS_ID))
        assert np.array_equal(back.sizes, s.sizes)
        if groups is None:
            assert back.groups is None
        else:
            assert back.groups.size == 2 and back.groups.method == "neighbours"
            assert back.groups.salt == SALT
            assert np.array_equal(back.groups.group_of, info.group_of)


def test_stats_blob_version_3_is_still_read_without_groups():
    v3 = _stats(None).to_bytes()
    assert int.from_bytes(v3[4:6], "little") == 3
    back = ClusterStats.from_bytes(v3)
    assert back.groups is None and back.sizes.tolist() == [5] * 8
    assert np.array_equal(back.counts[0][:, 1], [2] * 8)
    # a v4 blob whose trailer says "no groups" (method code 0) reads the same
    v4 = v3[:4] + (4).to_bytes(2, "little") + v3[6:] + bytes(21)
    back4 = ClusterStats.from_bytes(v4)
    assert back4.groups is None and back4.sizes.tolist() == [5] * 8
    old = bytearray(v3)
    old[4:6] = (2).to_bytes(2, "little")
    with pytest.raises(ValueError):
        ClusterStats.from_bytes(bytes(old))


def test_stats_blob_with_a_broken_grouping_is_refused():
    s = _stats(GroupInfo(2, "random", SALT, build_groups(KEY, SALT, 8, 2)))
    raw = bytearray(s.to_bytes())
    raw[-4:] = (99).to_bytes(4, "little")  # a group id out of range
    with pytest.raises(ValueError):
        ClusterStats.from_bytes(bytes(raw))
    bad_code = bytearray(s.to_bytes())
    bad_code[-(32 + 21)] = 9  # the method code byte of the trailer
    with pytest.raises(ValueError):
        ClusterStats.from_bytes(bytes(bad_code))


def test_default_grouping_is_neighbours_and_groups_are_off():
    import inspect

    from ombra.client import Client
    from ombra.collection import Collection

    for fn in (Client.create_collection, Collection.create):
        params = inspect.signature(fn).parameters
        assert params["decoy_group_size"].default == 1
        assert params["decoy_grouping"].default == "neighbours"
