# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Filtered recall@10 of integer range filters, option A (buckets) against option D (two digits).

    python core/bench/range_filters.py --cache bench-cache/range --out <dir> [--preset fast]
        [--queries 60] [--dist uniform skewed]

Local mode on the CPU (Client.local). Dataset: banking77 train utterances (CC-BY-4.0, 10,003
vectors, d = 384, MiniLM embeddings) with a synthetic integer attribute v in 0..4095:
  * uniform: v ~ U{0..4095}
  * skewed:  v = min(4095, round(Exponential(mean 400)))  (heavy low end, long tail; many ties)
One collection per (distribution, preset) holds the same v three times: `vq` (A, quantile edges,
64 buckets), `ve` (A, equal-width edges, 64 buckets of 64) and `vd` (D, two digits). Only the
filtered field matters in a query, so the three methods are compared on the same vectors, queries
and ranges. Ranges are random windows of the value distribution with a target share of 1%, 10%,
50% of the vectors. Ground truth: brute-force exact filtered top-10 in float64.

The collection (keys and ciphertexts) is cached under --cache and reused. Only aggregates are
written; the data is public text with synthetic integers.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "client"))

import ombra  # noqa: E402
from ombra.ranges import equal_width_edges, quantile_edges  # noqa: E402

K = 10
SEED = 20261115
TARGETS = (0.01, 0.10, 0.50)
HF = "https://huggingface.co/datasets/PolyAI/banking77/resolve/main/data/"
FLUSH = 1e-9
PASS = "bench"  # noqa: S105


def _flush(v: np.ndarray) -> np.ndarray:
    v = np.where(np.abs(v) < FLUSH, 0.0, v)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def load(cache: Path, n_queries: int) -> tuple[np.ndarray, np.ndarray]:
    npz = cache / "emb.npz"
    if npz.exists():
        z = np.load(npz)
        return z["x"], z["q"]
    import pyarrow.parquet as pq
    from sentence_transformers import SentenceTransformer

    cache.mkdir(parents=True, exist_ok=True)
    tr, te = cache / "train.parquet", cache / "test.parquet"
    for name, path in (("train-00000-of-00001.parquet", tr), ("test-00000-of-00001.parquet", te)):
        if not path.exists():
            urllib.request.urlretrieve(HF + name, path)  # noqa: S310
    a, b = pq.read_table(tr).to_pydict(), pq.read_table(te).to_pydict()
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu")
    model.max_seq_length = 128
    enc = lambda t: _flush(  # noqa: E731
        np.asarray(model.encode(t, batch_size=128, normalize_embeddings=True), dtype=np.float64)
    )
    x = enc(a["text"])
    rng = np.random.default_rng(SEED)
    qi = rng.choice(len(b["text"]), min(1000, len(b["text"])), replace=False)
    q = enc([b["text"][i] for i in qi])
    np.savez(npz, x=x, q=q)
    return x, q


def values(dist: str, n: int) -> np.ndarray:
    rng = np.random.default_rng(SEED + (dist == "skewed"))
    if dist == "uniform":
        return rng.integers(0, 4096, n)
    return np.minimum(4095, np.rint(rng.exponential(400, n))).astype(np.int64)


def build(root: Path, dist: str, preset: str, x: np.ndarray, v: np.ndarray):
    d = root / f"{dist}-{preset}"
    client = ombra.Client.local(d / "data")
    name = "r"
    if (d / "done").exists():
        client.open_collection(name, key_file=d / "k.key", passphrase=PASS)
        return client, 0.0
    meta = [{"vq": int(a), "ve": int(a), "vd": int(a)} for a in v]
    t0 = time.perf_counter()
    client.create_collection(
        name,
        x,
        [f"v{i}" for i in range(len(x))],
        key_file=d / "k.key",
        passphrase=PASS,
        preset=preset,
        metadata=meta,
        range_fields={
            "vq": quantile_edges(v),
            "ve": equal_width_edges(0, 4095),
            "vd": {"kind": "two_digit"},
        },
        n_clusters=6,
        scrypt_params=(2**14, 8, 1),
    )
    (d / "done").write_text("ok")
    return client, time.perf_counter() - t0


def windows(v: np.ndarray, share: float, n: int, rng: np.random.Generator):
    sv, m = np.sort(v), max(1, round(share * len(v)))
    out = []
    while len(out) < n:
        s = int(rng.integers(0, len(sv) - m + 1))
        lo, hi = int(sv[s]), int(sv[s + m - 1])
        got = int(((v >= lo) & (v <= hi)).sum())
        if 0.5 * m <= got <= 2.0 * m or share >= 0.5:
            out.append((lo, hi, got))
    return out


def truth(x, q, keep):
    idx = np.nonzero(keep)[0]
    s = x[idx] @ q
    return set(idx[np.argsort(-s, kind="stable")[:K]].tolist())


def run(client, x, q_all, v, share, field, wins, bucket_fp):
    col = client._core._get("r")[0]
    rec, inc, lat, fetched, rej, matches, rounds = [], 0, [], [], [], [], []
    for qi, (lo, hi, _) in enumerate(wins):
        q = q_all[qi]
        keep = (v >= lo) & (v <= hi)
        t = truth(x, q, keep)
        plan = col.schema.plan({field: {"gte": lo, "lte": hi}})
        t0 = time.perf_counter()
        res = col.search(q, K, range_plan=plan)
        lat.append(time.perf_counter() - t0)
        got = {int(h.id[1:]) for h in res.hits}
        rec.append(len(got & t) / max(1, min(K, len(t))))
        inc += res.incomplete
        fetched.append(res.fetched)
        rej.append(res.rejected / max(1, min(res.matches, res.fetched)))
        matches.append(res.matches)
        rounds.append(res.rounds)
    return {
        "recall": float(np.mean(rec)),
        "incomplete": inc / len(wins),
        "fetched": float(np.mean(fetched)),
        "edge_fp_rate_fetched": float(np.mean(rej)),
        "edge_fp_rate_data": bucket_fp,
        "candidates_mean": float(np.mean(matches)),
        "latency_ms": 1000 * statistics.median(lat),
        "latency_ms_mean": 1000 * float(np.mean(lat)),
    }


def bucket_fp_rate(field_kind, edges, v, lo, hi):
    """Share of records passing the encrypted-side test that are outside [lo, hi] (data level)."""
    inr = (v >= lo) & (v <= hi)
    if field_kind == "buckets":
        b = np.searchsorted(edges, v, side="right")
        passed = (b >= np.searchsorted(edges, lo, "right")) & (
            b <= np.searchsorted(edges, hi, "right")
        )
    else:
        c, f = v // 64, v % 64
        cl, ch = lo // 64, hi // 64
        w_c = np.where((c > cl) & (c < ch), 2, np.where((c == cl) | (c == ch), 1, 0))
        if cl == ch:
            fw = (f >= lo % 64) & (f <= hi % 64)
        else:
            fw = (f >= lo % 64) | (f <= hi % 64)
        passed = (w_c + fw) >= 2
    return float(1 - inr.sum() / max(1, passed.sum()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="bench-cache/range")
    ap.add_argument("--out", default="bench-cache/out")
    ap.add_argument("--preset", default="fast")
    ap.add_argument("--queries", type=int, default=60)
    ap.add_argument("--dist", nargs="+", default=["uniform", "skewed"])
    a = ap.parse_args()
    cache, out = Path(a.cache), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    x, q_all = load(cache, a.queries)
    results = {"preset": a.preset, "queries": a.queries, "n": len(x), "cells": []}
    for dist in a.dist:
        v = values(dist, len(x))
        client, build_s = build(cache, dist, a.preset, x, v)
        print(f"{dist}: built in {build_s:.0f} s", flush=True)
        eq_edges = equal_width_edges(0, 4095)
        q_edges = quantile_edges(v)
        for share in TARGETS:
            wins = windows(v, share, a.queries, np.random.default_rng(SEED + int(share * 1000)))
            for field, kind, edges in (
                ("vq", "buckets", q_edges),
                ("ve", "buckets", eq_edges),
                ("vd", "two_digit", None),
            ):
                fp = float(np.mean([bucket_fp_rate(kind, edges, v, lo, hi) for lo, hi, _ in wins]))
                r = run(client, x, q_all, v, share, field, wins, fp)
                r.update(
                    dist=dist,
                    share=share,
                    actual_share=float(np.mean([g for *_, g in wins]) / len(v)),
                    method={"vq": "A quantile", "ve": "A equal-width", "vd": "D two digits"}[field],
                )
                results["cells"].append(r)
                print(json.dumps(r), flush=True)
                (out / f"range-{a.preset}.json").write_text(json.dumps(results, indent=1))
        client.close()


if __name__ == "__main__":
    main()
