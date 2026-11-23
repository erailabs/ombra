# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Fixed decoy groups: the clusters of a collection are split into groups of `g`, and a Decoy query
asks for whole groups instead of single clusters plus fresh random decoys.

Related queries share their real clusters, so with per-query random decoys the intersection of a
few requests shows the real clusters. With fixed groups the server can at best learn which groups
were wanted, never which cluster of a group; and the extra (decoy) groups of a query are a keyed
function of its real group set, so they are repeatable.

All randomness here is a keyed PRF, no new primitive: HKDF-SHA256 derives sub-keys from the
collection's master key (the client's secret material), HMAC-SHA256 is the PRF. A permutation is the
sort of the ids by their PRF value. The server never sees the key, so it cannot tell which
clusters share a group, which groups are real, or the order a request was built in.

PRF inputs (every part is length-prefixed, so two inputs never collide by concatenation):
* grouping:      HMAC(k_group, b"perm" | cluster id)
* decoy groups:  HMAC(k_decoy, b"decoy" | round | sorted real group ids | group id)
* request order: HMAC(k_order, b"order" | round | sorted real group ids | cluster id)
where the sub-keys are HKDF(master_key, salt=<salt stored in the sealed stats>, info=<label>), and
"sorted real group ids" are the real groups of the query up to and including this round.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

METHODS = ("random", "neighbours")
SALT_LEN = 16
_INFO = b"ombra/decoy-groups/v1/"


def _subkey(master_key: bytes, salt: bytes, label: bytes) -> bytes:
    return HKDF(hashes.SHA256(), 32, salt, _INFO + label).derive(bytes(master_key))


def _prf(key: bytes, *parts: bytes) -> bytes:
    h = hmac.new(key, digestmod=hashlib.sha256)
    for p in parts:
        h.update(struct.pack("<I", len(p)))
        h.update(p)
    return h.digest()


def _u32(*vals: int) -> bytes:
    return struct.pack(f"<{len(vals)}I", *vals)


def _ranked(key: bytes, tag: bytes, prefix: bytes, ids: Sequence[int]) -> list[int]:
    """`ids` sorted by their PRF value: a keyed permutation."""
    return sorted(ids, key=lambda i: _prf(key, tag, prefix, _u32(i)))


def build_groups(
    master_key: bytes,
    salt: bytes,
    n_clusters: int,
    group_size: int,
    method: str = "random",
    centers: np.ndarray | None = None,
) -> np.ndarray:
    """Group id (0..n/g-1) of every cluster; `n_clusters` must be a multiple of `group_size`.

    "random": the keyed permutation of the cluster ids, cut into groups of g.
    "neighbours": a greedy partition on centroid distance. Walking the clusters in the keyed
    permutation order, the first one not yet grouped is an anchor and takes its g - 1 nearest
    ungrouped centers (ties by id). Deterministic given the key, the salt and the centers."""
    g = int(group_size)
    if g < 1 or n_clusters % g:
        raise ValueError("the cluster count must be a positive multiple of the group size")
    if method not in METHODS:
        raise ValueError(f"decoy grouping must be one of {list(METHODS)}")
    k = _subkey(master_key, salt, b"group")
    perm = _ranked(k, b"perm", b"", range(n_clusters))
    out = np.full(n_clusters, -1, dtype=np.int64)
    if method == "random" or g == 1:
        for i, c in enumerate(perm):
            out[c] = i // g
        return out
    if centers is None or len(centers) != n_clusters:
        raise ValueError("neighbour grouping needs the cluster centers")
    x = np.asarray(centers, dtype=np.float64)
    free = np.ones(n_clusters, dtype=bool)
    gid = 0
    for a in perm:
        if not free[a]:
            continue
        free[a] = False
        out[a] = gid
        rest = np.nonzero(free)[0]
        d2 = ((x[rest] - x[a]) ** 2).sum(axis=1)
        take = rest[np.lexsort((rest, d2))[: g - 1]]  # nearest first, ties by id
        out[take] = gid
        free[take] = False
        gid += 1
    return out


def members_of(group_of: np.ndarray) -> list[list[int]]:
    """Clusters of every group, ascending."""
    out: list[list[int]] = [[] for _ in range(int(group_of.max()) + 1 if len(group_of) else 0)]
    for c, g in enumerate(group_of.tolist()):
        out[g].append(c)
    return out


@dataclass
class GroupInfo:
    """The fixed grouping of a collection, kept in the sealed cluster statistics."""

    size: int
    method: str
    salt: bytes
    group_of: np.ndarray  # (C,) group id of every cluster

    def __post_init__(self) -> None:
        self.group_of = np.asarray(self.group_of, dtype=np.int64)
        n, g = len(self.group_of), self.size
        counts = np.bincount(self.group_of, minlength=max(n // max(g, 1), 1)) if n else []
        if g < 2 or n % g or len(counts) != n // g or any(c != g for c in counts):
            raise ValueError("decoy groups must partition the clusters into groups of g")
        if self.method not in METHODS or len(self.salt) != SALT_LEN:
            raise ValueError("invalid decoy grouping")


class GroupRequester:
    """Builds the requested cluster list of a Decoy round from the real groups. Pure and
    repeatable: the same real group set, round and already-asked groups give identical bytes."""

    def __init__(self, master_key: bytes, salt: bytes, group_of: np.ndarray) -> None:
        self._k_decoy = _subkey(master_key, salt, b"decoy")
        self._k_order = _subkey(master_key, salt, b"order")
        self.group_of = group_of
        self.members = members_of(group_of)
        self.real_all: set[int] = set()  # real groups of this query so far

    def request(
        self, round_no: int, chosen: Sequence[int], n_groups: int, asked: set[int]
    ) -> list[int]:
        """Clusters to ask in round `round_no`: the whole group of every chosen cluster not yet
        asked, plus decoy groups up to `n_groups` groups in total, in a keyed order. `asked` are
        the clusters already requested on this stream."""
        asked_groups = {int(self.group_of[c]) for c in asked}
        real = sorted({int(self.group_of[c]) for c in chosen} - asked_groups)
        if len(real) > n_groups:
            raise RuntimeError("more real groups than the round holds")
        self.real_all.update(real)
        prefix = _u32(round_no, *sorted(self.real_all))
        cand = [g for g in range(len(self.members)) if g not in asked_groups and g not in set(real)]
        decoys = _ranked(self._k_decoy, b"decoy", prefix, cand)[: n_groups - len(real)]
        clusters = [c for g in real + decoys for c in self.members[g]]
        return _ranked(self._k_order, b"order", prefix, clusters)
