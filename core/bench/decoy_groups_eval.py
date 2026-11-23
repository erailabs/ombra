# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Decoy padding versus fixed decoy groups: intersection attack, recall@10 and server work.

    python core/bench/decoy_groups_eval.py simulate [--n 30000] [--clusters 256] [--out f.json]
    python core/bench/decoy_groups_eval.py validate [--n 20000] [--clusters 256] [--queries 4]

CPU only, synthetic data, no plaintext printed (aggregates only).

`simulate` runs the real client search code (`ombra.search.run_search`: `ProbePlanner`, the group
logic and the request builder) against a fake server that answers the plaintext center scores, so
there is no encryption. That is valid because the clusters a Decoy query requests depend only on
the center scores (the filter, when there is one, only enters through the stats). It measures, per
mode (old random padding, random groups, neighbouring-centre groups; g in --group-sizes):
  * the intersection attack of `leakage.py` on 1, 10, 100 and 1000 related queries (a base query
    plus noise), scored against the clusters the base query really searched ("real", group-closed)
    and against the base query's best `first` clusters ("wanted", what the user's query is near);
  * recall@10 of the encrypted search's candidate set: the exact top 10 within the requested
    clusters against the exact top 10 of the whole collection, and the clusters scored per query.
`validate` creates a real local-mode collection (encrypted, CPU), runs real searches and checks
that the requested clusters equal the simulation's, and that the recall agrees.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("leakage", _here / "leakage.py")
lk = importlib.util.module_from_spec(_spec)
sys.modules["leakage"] = lk
_spec.loader.exec_module(lk)

KEY, SALT = b"decoy-eval-master-key-0123456789"[:32], b"decoy-eval-salt!"
MODES = ("old", "random", "neighbours")
NS = (1, 10, 100, 1000)


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def synthetic(
    n: int,
    d: int,
    seed: int,
    supers: int = 32,
    subs: int = 16,
    sub_noise: float = 0.8,
    pt_noise: float = 0.6,
) -> np.ndarray:
    """Two-level topics: `supers` broad topics with `subs` sub-topics each, so that nearby cluster
    centers exist, as in real embeddings. Unit vectors."""
    rng = np.random.default_rng(seed)
    top = _unit(rng.standard_normal((supers, d)))
    sub = _unit(
        np.repeat(top, subs, axis=0)
        + sub_noise * rng.standard_normal((supers * subs, d)) / math.sqrt(d)
    )
    pts = sub[rng.integers(0, len(sub), n)] + pt_noise * rng.standard_normal((n, d)) / math.sqrt(d)
    return _unit(pts)


# ---- the real search code against a fake server --------------------------------------------------
class _Msg(SimpleNamespace):
    def WhichOneof(self, _: str) -> str:  # noqa: N802
        return self.kind


def _ns(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**kw)


class PlanCol:
    """A collection stand-in for `run_search`: answers plaintext center scores `cs` and one
    always-matching vector per requested cluster. Records the requested clusters."""

    def __init__(self, n_clusters: int, dim: int, groups: Any, key: bytes = KEY) -> None:
        from ombra.probe import ClusterStats

        self.requests: list[list[int]] = []
        self.cs = np.zeros(n_clusters)
        stats = ClusterStats.empty(n_clusters, ())
        stats.groups = groups
        outer = self

        def search_stream(it):
            next(it)
            blob = outer.cs.astype(np.float64).tobytes()
            yield _Msg(kind="centers", centers=_ns(score_blocks=[blob], center_count=n_clusters))
            for req in it:
                outer.requests.append(list(req.chosen.cluster_ids))
                for c in req.chosen.cluster_ids:
                    yield _Msg(
                        kind="scores",
                        scores=_ns(
                            cluster_id=c,
                            score_blocks=[np.full(1, 0.1).tobytes()],
                            ids=[b"x"],
                            block_sizes=[1],
                        ),
                    )

        self.pb = _ns(
            ListBlocksRequest=lambda **kw: None,
            SearchStart=lambda **kw: None,
            SearchRequest=lambda start=None, chosen=None: _ns(start=start, chosen=chosen),
            SearchChosen=lambda cluster_ids: _ns(cluster_ids=cluster_ids),
        )
        self.stub = _ns(Search=search_stream)
        self.kit = _ns(
            key_tag="t",
            master_key=key,
            encrypt_query=lambda ext: b"",
            decrypt_column=lambda env: np.frombuffer(env, dtype=np.float64),
        )
        self.name, self.security_level, self.derived = "sim", "decoy", _ns(dimension=dim)
        self.cards, self.cluster_count, self.stats = (), n_clusters, stats


def plan(
    col: PlanCol, cs: np.ndarray, q: np.ndarray, rng: random.Random, n_probe: int | None = None
) -> tuple[list, set]:
    """Requested clusters per round and the real (searched) clusters of one Decoy query."""
    from ombra import search

    col.cs, col.requests = cs, []
    saved = search._rerank
    search._rerank = lambda col_, q_, top, k: []  # no record blobs in the simulation
    try:
        res = search.run_search(col, q, 10, level="decoy", n_probe=n_probe, _rng=rng)
    finally:
        search._rerank = saved
    return col.requests, set(res.probed)


def make_groups(mode: str, g: int, centers: np.ndarray) -> Any:
    from ombra.decoy_groups import GroupInfo, build_groups

    if mode == "old":
        return None
    return GroupInfo(g, mode, SALT, build_groups(KEY, SALT, len(centers), g, mode, centers))


def _first(n_clusters: int) -> int:
    return max(1, math.ceil(math.sqrt(n_clusters)))


# ---- simulation ------------------------------------------------------------------------------
def simulate(args: argparse.Namespace) -> dict[str, Any]:
    from ombra.clustering import cluster_vectors

    x = synthetic(args.n, args.d, args.seed, sub_noise=args.sub_noise, pt_noise=args.pt_noise)
    cl = cluster_vectors(x, args.clusters, seed=args.seed)
    centers = cl.centers.astype(np.float64)
    c = len(centers)
    first = args.first or _first(c)
    rng = np.random.default_rng(args.seed + 1)
    xf = x.astype(np.float32)
    # one set of base queries, related queries and recall queries for every mode
    bases = [x[int(rng.integers(0, args.n))] for _ in range(args.trials)]
    related = [
        [
            _unit(b + args.noise * rng.standard_normal(b.shape) / math.sqrt(args.d))
            for _ in range(max(args.ns))
        ]
        for b in bases
    ]
    rq = [
        _unit(
            x[int(rng.integers(0, args.n))]
            + args.recall_noise * rng.standard_normal(args.d) / math.sqrt(args.d)
        )
        for _ in range(args.recall_queries)
    ]
    truth = [np.argsort(-(xf @ q.astype(np.float32)))[:10] for q in rq]
    labels = cl.labels
    out: dict[str, Any] = {
        "params": {
            "n": args.n,
            "d": args.d,
            "clusters": c,
            "first": first,
            "noise": args.noise,
            "recall_noise": args.recall_noise,
            "sub_noise": args.sub_noise,
            "pt_noise": args.pt_noise,
            "trials": args.trials,
            "ns": list(args.ns),
            "recall_queries": args.recall_queries,
        },
        "modes": {},
    }
    configs = [("old", 1)] + [(m, g) for m in ("random", "neighbours") for g in args.group_sizes]
    for mode, g in configs:
        name = "old" if mode == "old" else f"{mode}-g{g}"
        col = PlanCol(c, args.d, make_groups(mode, g, centers))
        prng = random.Random(args.seed)  # noqa: S311
        acc = {
            n: {
                k: {
                    "size": 0.0,
                    "success": 0.0,
                    "precision": 0.0,
                    "recall": 0.0,
                    "wanted_precision": 0.0,
                    "wanted_recall": 0.0,
                }
                for k in ("first_round", "union")
            }
            for n in args.ns
        }
        for t, base in enumerate(bases):
            _, real = plan(col, centers @ base, base, prng, args.first)
            wanted = {int(i) for i in np.argsort(-(centers @ base), kind="stable")[:first]}
            reqs = []
            for q in related[t]:
                rounds, _ = plan(col, centers @ q, q, prng, args.first)
                reqs.append([set(r) for r in rounds])
            for n in args.ns:
                res = lk.intersection_attack(reqs[:n], real)
                wres = lk.intersection_attack(reqs[:n], wanted)
                for kind in ("first_round", "union"):
                    for key in ("size", "success", "precision", "recall"):
                        acc[n][kind][key] += float(res[kind][key]) / args.trials
                    acc[n][kind]["wanted_precision"] += wres[kind]["precision"] / args.trials
                    acc[n][kind]["wanted_recall"] += wres[kind]["recall"] / args.trials
        # recall@10 and work
        hits, work, real_n = 0.0, [], []
        for q, tr in zip(rq, truth, strict=True):
            rounds, real = plan(col, centers @ q, q, prng, args.first)
            asked = np.array(sorted({cc for r in rounds for cc in r}))
            work.append(len(asked))
            real_n.append(len(real))
            rows = np.nonzero(np.isin(labels, asked))[0]
            top = rows[np.argsort(-(xf[rows] @ q.astype(np.float32)))[:10]]
            hits += len(set(top.tolist()) & set(tr.tolist())) / 10
        out["modes"][name] = {
            "attack": {str(n): v for n, v in acc.items()},
            "recall_at_10": hits / len(rq),
            "clusters_scored_per_query": float(np.mean(work)),
            "real_clusters_per_query": float(np.mean(real_n)),
        }
        print(f"done {name}", file=sys.stderr)
    # reference: no padding at all, the best `first` clusters only (what Fast would search)
    hits = 0.0
    for q, tr in zip(rq, truth, strict=True):
        asked = np.argsort(-(centers @ q), kind="stable")[:first]
        rows = np.nonzero(np.isin(labels, asked))[0]
        top = rows[np.argsort(-(xf[rows] @ q.astype(np.float32)))[:10]]
        hits += len(set(top.tolist()) & set(tr.tolist())) / 10
    out["reference_fast_first_round_recall_at_10"] = hits / len(rq)
    return out


def markdown(rep: dict[str, Any]) -> str:
    p = rep["params"]
    lines = [
        f"n={p['n']} d={p['d']} clusters={p['clusters']} first={p['first']} noise={p['noise']} "
        f"trials={p['trials']}",
        "",
        "| mode | recall@10 | clusters scored | real clusters |",
        "| --- | --- | --- | --- |",
    ]
    for name, m in rep["modes"].items():
        lines.append(
            f"| {name} | {m['recall_at_10']:.3f} | {m['clusters_scored_per_query']:.0f} "
            f"| {m['real_clusters_per_query']:.1f} |"
        )
    for kind in ("first_round", "union"):
        lines += [
            "",
            f"Attack on the {kind} requests (mean over trials; success = rate)",
            "",
            "| mode | n | size | success | precision | recall | prec. wanted | recall wanted |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for name, m in rep["modes"].items():
            for n, v in m["attack"].items():
                r = v[kind]
                lines.append(
                    f"| {name} | {n} | {r['size']:.1f} | {r['success']:.2f} | {r['precision']:.2f} "
                    f"| {r['recall']:.2f} | {r['wanted_precision']:.2f} "
                    f"| {r['wanted_recall']:.2f} |"
                )
    return "\n".join(lines)


# ---- validation against the real, encrypted local mode -----------------------------------------
def validate(args: argparse.Namespace) -> dict[str, Any]:
    import tempfile

    import ombra

    x = synthetic(args.n, args.d, args.seed, sub_noise=args.sub_noise, pt_noise=args.pt_noise)
    xf = x.astype(np.float32)
    rng = np.random.default_rng(args.seed + 5)
    qs = [
        _unit(
            x[int(rng.integers(0, args.n))]
            + args.recall_noise * rng.standard_normal(args.d) / math.sqrt(args.d)
        )
        for _ in range(args.queries)
    ]
    ids = [f"synthetic-record-{i:08d}" for i in range(args.n)]
    out: dict[str, Any] = {
        "params": {"n": args.n, "d": args.d, "clusters": args.clusters},
        "configs": {},
    }
    for mode, g in (("old", 1), ("random", 4), ("neighbours", 4)):
        with tempfile.TemporaryDirectory() as t:
            client = ombra.Client.local(Path(t) / "data")
            try:
                kw = {} if mode == "old" else {"decoy_group_size": g, "decoy_grouping": mode}
                client.create_collection(
                    "v",
                    x,
                    ids,
                    key_file=Path(t) / "v.key",
                    passphrase="validate",  # noqa: S106
                    scrypt_params=(2**14, 8, 1),
                    n_clusters=args.clusters,
                    **kw,
                )
                col, _ = client._core._open["v"]
                info = col.stats.groups
                sim = PlanCol(col.cluster_count, args.d, info, col.kit.master_key)
                same, same_order, rec_sim, rec_res = 0, 0, [], []
                for q in qs:
                    from ombra.search import run_search

                    view = lk.record(
                        client,
                        lambda q=q, col=col: run_search(col, q, 10, level="decoy"),
                        keep_payloads=False,
                    )
                    real_rounds = view.requested_clusters()
                    res = run_search(col, q, 10, level="decoy")
                    sim_rounds, _ = plan(sim, np.asarray(col.centers) @ q, q, random.Random(0))  # noqa: S311
                    same += int([set(r) for r in real_rounds] == [set(r) for r in sim_rounds])
                    same_order += int(real_rounds == sim_rounds)
                    asked = np.array(sorted({c for r in sim_rounds for c in r}))
                    # plaintext recall from the clusters of the (identical) request
                    lab = _labels(col, ids)
                    rows = np.nonzero(np.isin(lab, asked))[0]
                    top = rows[np.argsort(-(xf[rows] @ q.astype(np.float32)))[:10]]
                    truth = np.argsort(-(xf @ q.astype(np.float32)))[:10]
                    rec_sim.append(len(set(top.tolist()) & set(truth.tolist())) / 10)
                    got = {int(h.id.decode().split("-")[-1]) for h in res.hits}
                    rec_res.append(len(got & set(truth.tolist())) / 10)
                out["configs"]["old" if mode == "old" else f"{mode}-g{g}"] = {
                    "queries": len(qs),
                    "requested_sets_identical": same,
                    "requested_order_identical": same_order,
                    "recall_plain_restricted": float(np.mean(rec_sim)),
                    "recall_encrypted_search": float(np.mean(rec_res)),
                }
            finally:
                client.close()
    return out


_LABELS: dict[int, np.ndarray] = {}


def _labels(col: Any, ids: list[str]) -> np.ndarray:
    """Cluster of every vector id, read from the collection's blocks (local mode, test only)."""
    key = id(col)
    if key not in _LABELS:
        lab = np.full(len(ids), -1)
        for b in col.block_stats():
            header, _cols = col._get_block(b.segment)
            for i, dead in zip(header.ids, header.deleted, strict=True):
                if not dead:
                    lab[int(bytes(i).decode().split("-")[-1])] = b.cluster
        _LABELS[key] = lab
    return _LABELS[key]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("simulate", "validate"):
        s = sub.add_parser(name)
        s.add_argument("--n", type=int, default=30000 if name == "simulate" else 20000)
        s.add_argument("--d", type=int, default=384)
        s.add_argument("--clusters", type=int, default=256)
        s.add_argument("--seed", type=int, default=7)
        s.add_argument(
            "--first", type=int, default=None, help="n_probe (default ceil(sqrt(clusters)))"
        )
        s.add_argument("--sub-noise", type=float, default=0.8, help="spread of sub-topics")
        s.add_argument("--pt-noise", type=float, default=0.6, help="spread of points")
        s.add_argument("--recall-noise", type=float, default=0.5)
        s.add_argument("--out", default=None)
        if name == "simulate":
            s.add_argument("--group-sizes", type=int, nargs="+", default=[2, 4, 8])
            s.add_argument("--ns", type=int, nargs="+", default=list(NS))
            s.add_argument("--trials", type=int, default=5)
            s.add_argument("--noise", type=float, default=0.05)
            s.add_argument("--recall-queries", type=int, default=300)
        else:
            s.add_argument("--queries", type=int, default=4)
    args = p.parse_args(argv)
    rep = simulate(args) if args.cmd == "simulate" else validate(args)
    text = json.dumps(rep, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(markdown(rep) if args.cmd == "simulate" else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
