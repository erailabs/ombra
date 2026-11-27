# Clusters and search levels

Scoring every vector is the cost that grows with the collection. Ombra groups similar vectors into clusters and scores only the clusters likely to hold the nearest neighbours.

## Making the clusters

When you create a collection, the client runs spherical k-means (faiss) on your plaintext vectors, in a short-lived child process that loads faiss and nothing else. The vectors pass through a pipe between the two local processes and are never written to disk. The server never sees the plaintext centers. It gets them encrypted, one ciphertext per dimension, in a block of their own ([encoding](encoding.md)).

The default number of clusters is

$$C = \operatorname{round}\!\left(\frac{N}{S}\right)$$

where $N$ is the number of vectors and $S$ the slots per ciphertext, which is also the vectors per block ({{ preset("fast", "batch_size") }} for Fast, {{ preset("balanced", "batch_size") }} for Balanced). A cluster averages about one full block. The centers must fit in one block, so $C \le S$. You can pass your own $C$. Empty clusters are dropped and the rest renumbered, so you can end up with fewer than you asked for.

`insert_vectors` puts each new vector in the cluster of its nearest existing center. The centers do not move, so after many inserts the clusters drift from the data ([re-clustering](deletes.md)).

## Two stages

Stage 1 asks the server for the score of the query against every center (one small block). The client sorts the clusters. Stage 2 asks the server to score the data blocks of the chosen clusters. The client keeps the best candidates, fetches their exact vectors from the encrypted record blobs and re-ranks in plaintext ([Encrypted search](encrypted-search.md)).

The first round asks for $p = \lceil \sqrt{C} \rceil$ clusters by default (`n_probe` overrides it). With $C = 100$ that is 10, a tenth of the work. The price is recall: a true neighbour in a cluster ranked below the first $p$ is missed ([accuracy](accuracy.md)).

## The three levels

The level is set per collection and can be overridden per query. It decides how many rounds a query makes and how much the cluster lists tell the server. At every level, the client then fetches the record blobs of its best candidates by id, which reveals which records were candidates and how large k is. No level hides that. See [what the server learns](../security/what-the-server-learns.md).

**Fast** asks only for the clusters it wants. An unfiltered query makes one round of $p$ clusters. If fewer than $k$ vectors came back, it asks for $2p$ new clusters, then $4p$, and so on, until it has $k$ vectors or has asked for $8p$ clusters (`max_probe_factor`) or all of them. A filtered query always makes exactly two rounds ([filters](filters.md)).

**Decoy** makes exactly two rounds for every query, each asking for a number of clusters that depends only on $p$ and $C$, never on the query or filter:

$$r_1 = \min(C,\ 2p) \qquad r_2 = \min(C-r_1,\ 4p)$$

Without a filter, the real clusters are the first $p$ by center score in round 1 and the next $2p$ in round 2. The rest are random clusters not asked before (decoys), and the client drops what they return. With $C = 100$, $p = 10$: 20 clusters in round 1 (10 real), 40 in round 2 (20 real). When $r_1 + r_2 \ge C$, Decoy is a full scan. A cluster is never asked for twice in one search, and the client shuffles each request. Over many similar queries, though, the real clusters repeat and the decoys change, so intersecting the sets finds the real ones ([Out of scope](../security/out-of-scope.md)).

**Decoy groups (optional, off by default).** Create the collection with `decoy_group_size=g` (g of 2 or more; the cluster count is rounded down to a multiple of g) to fix the decoys. The client splits the clusters into groups with a keyed permutation (a key only the client holds) and keeps the grouping in the encrypted statistics blob. A Decoy query asks for whole groups: each real cluster brings in its group, and the rest come from groups chosen by a keyed function of the real groups and the round, so the same real groups always give the same request. `decoy_grouping` is `"neighbours"` (default: clusters with close centers share a group) or `"random"`. Recall can drop, since part of each group is far from the query: on a synthetic test (30,000 vectors, d = 384, 256 clusters), recall@10 at 4 probes was 0.998 without groups, 0.990 with `"neighbours"` and g=4, and 0.75 with `"random"` and g=4; at 16 probes all reached 1.0. Use `decoy_group_size=4` with `"neighbours"`.

**Strict** scans every cluster on every query, in two rounds: the first half of the cluster ids, then the second half. Nothing in the request depends on the query, so the choice of clusters tells the server nothing. The cost is a full scan that grows linearly with the collection. Use it for small collections or when you need that guarantee.

!!! deep-dive "Rare filters turn Fast and Decoy into a full scan"
    When a filtered query has cluster statistics and the client expects fewer than 0.5% of all vectors to match (`min_selectivity` in `probe.py`), it scans everything in two rounds with no decoys, because a plan built on a handful of expected matches kept missing them. The server sees a full unpadded scan, which tells it the filter was rare. The result reports it as `strict_fallback`.

## Speed

Strict and the rare-filter fallback score every block, so their time grows with the collection. Decoy scores up to $6p$ clusters against Fast's $p$ to $2p$. Measured times: TBD, see [Performance](../performance/index.md).
