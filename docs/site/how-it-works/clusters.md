# Clusters and search levels

Scoring every vector against the query is the cost that grows with the collection. Ombra groups similar vectors into clusters and scores only the clusters likely to hold the nearest neighbours.

## Making the clusters

When you create a collection, the client runs spherical k-means (faiss) on your plaintext vectors. Vectors have unit length, so the nearest center is the one with the largest dot product, and the centers are unit vectors too. K-means runs on your machine, in a short-lived child process of the client that loads faiss and nothing else, on vectors the client already holds. The vectors pass through a pipe between the two local processes and are never written to disk. The server never sees the plaintext centers. It gets them encrypted, one ciphertext per dimension, in a block of their own (see [encoding](encoding.md) for how a block is laid out).

The default number of clusters is

$$C = \operatorname{round}\!\left(\frac{N}{S}\right)$$

where $N$ is the number of vectors and $S$ is the number of slots per ciphertext, which is also the number of vectors one block holds. $S$ is {{ preset("fast", "batch_size") }} for Fast and {{ preset("balanced", "batch_size") }} for Balanced. A cluster averages about one full block. The centers of all clusters must fit in one block, so $C$ cannot exceed $S$. You can pass your own $C$. Empty clusters are dropped and the rest renumbered, so you can end up with fewer than you asked for.

Example: $N = 100{,}000$ vectors on Balanced ($S = {{ preset("balanced", "batch_size") }}$) gives $C = \operatorname{round}(100000 / {{ preset("balanced", "batch_size") }}) = {{ (100000 / preset("balanced", "batch_size")) | round | int }}$ clusters of about {{ (100000 / ((100000 / preset("balanced", "batch_size")) | round | int)) | round | int }} vectors each.

`insert_vectors` puts each new vector in the cluster of its nearest existing center. The centers do not move, so after many inserts the clusters drift away from the data. [Deletes and compaction](deletes.md) covers re-clustering, which fixes that.

## Two stages

Stage 1 asks the server for the score of the query against every center. That is one small block. The client decrypts the $C$ scores and sorts the clusters from best to worst.

Stage 2 asks the server to score the data blocks of the clusters the client picked. The client decrypts those scores, keeps the best candidates, fetches their exact vectors from the encrypted record blobs and re-ranks them in plaintext. [Encrypted search](encrypted-search.md) explains what the server computes for each block.

The first round asks for

$$p = \left\lceil \sqrt{C} \right\rceil$$

clusters by default (`n_probe` overrides it). With $C = 12$ that is $\lceil 3.46 \rceil = 4$ clusters, a third of the work of scanning everything. With $C = 100$ it is 10, a tenth.

The price is recall. A true neighbour in a cluster ranked below the first $p$ is missed. The extra rounds below exist to reduce that, and [accuracy](accuracy.md) has the measured numbers.

## The three levels

The level is set per collection and can be overridden per query. It decides how many rounds a query makes and how much the cluster lists tell the server. It covers only which clusters a query requests. After every search, at every level, the client fetches the record blobs of its best candidates by id, and the server knows the cluster of each id. That fetch reveals which records were candidates and how large k is, and no level hides it. See [what the server learns](../security/what-the-server-learns.md).

**Fast** asks only for the clusters it wants. An unfiltered query makes one round of $p$ clusters. If fewer than $k$ vectors came back ($k$ is the number of results you asked for), it makes another round of $2p$ new clusters, then $4p$, and so on, until it has $k$ vectors or has asked for $8p$ clusters (`max_probe_factor`) or all of them. A filtered query always makes exactly two rounds, because the filter decides how many clusters it needs ([filters](filters.md)).

**Decoy** makes exactly two rounds for every query, and each round asks for a number of clusters that depends only on $p$ and $C$, never on the query or the filter:

$$r_1 = \min(C,\ 2p) \qquad r_2 = \min(C-r_1,\ 4p)$$

Without a filter, the real clusters are the first $p$ by center score in round 1 and the next $2p$ in round 2. The remaining places in each round are filled with random clusters that were not asked before, the decoys. The client drops whatever the decoys return.

Example with $C = 100$ and $p = 10$: round 1 asks for $\min(100, 20) = 20$ clusters, 10 real and 10 decoys. Round 2 asks for $\min(80, 40) = 40$, 20 real and 20 decoys. That is 60 of 100 clusters scored to get 30 real ones, twice the scoring work the real clusters need. With $C = 12$ and $p = 4$ the rounds ask for 8 and then 4, which is every cluster, so Decoy there is a full scan.

A cluster is never asked for twice on one search. A repeat would show the server which of the earlier choices were decoys.

The client shuffles the list of clusters of every request before it sends it, so the position of an id says nothing about whether it is real. A server still sees one set of clusters per query. Over many similar queries the real clusters repeat and the decoys change, so the real ones can be found by intersecting the sets. See [Out of scope](../security/out-of-scope.md).

**Decoy groups (optional, off by default).** The decoys above are fresh for every query. You can instead fix the decoys: when you create a collection with `decoy_group_size=g` (g of 2 or more; the cluster count is rounded down to a multiple of g), the client splits the clusters into groups of g with a keyed permutation, using a key only the client holds, and keeps the grouping in the encrypted statistics blob. A Decoy query then asks for whole groups: every real cluster brings in its whole group, and the remaining places come from other groups chosen by a keyed function of the real groups and the round number. The same real groups always give the same request, in the same order. Each round asks for $\lceil 2p/g \rceil$ and then $\lceil 4p/g \rceil$ groups, so the counts are multiples of g and still do not depend on the query. `decoy_grouping` picks how the groups are built: `"neighbours"` (the default: clusters whose centers are close share a group) or `"random"` (a keyed random partition). The server does about the same amount of scoring as before, but part of each group is made of clusters that are not near the query, so fewer of the query's own nearest clusters fit in the round and recall can drop. `"neighbours"` loses less, because the clusters near a query's best cluster often sit in the same group. On a synthetic test (30,000 vectors of 384 dimensions, 256 clusters), recall@10 at 4 probes was 0.998 without groups, 0.990 with `"neighbours"` and g=4, and 0.75 with `"random"` and g=4; at 16 probes all three reached 1.0. If you turn groups on, `decoy_group_size=4` with the default `"neighbours"` is the recommended setting. Collections created without the option behave exactly as described above. What this hides and what it does not is in [What the server learns](../security/what-the-server-learns.md).

**Strict** scans every cluster on every query, in two rounds: the first half of the cluster ids, then the second half. Nothing in the request depends on the query, so the choice of clusters tells the server nothing. The cost is a full scan. With $C = 100$, Strict scores 100 clusters where a Fast first round scores 10, and the work grows linearly with the collection. Use it for small collections or when you need that guarantee.

!!! deep-dive "Rare filters turn Fast and Decoy into a full scan"
    When a filtered query has cluster statistics and the client expects fewer than 0.5% of all vectors to match, it stops planning clusters and scans everything, in two rounds split by cluster id, with no decoys. The threshold is `min_selectivity` in `probe.py`. The reason is recall: a plan built on a handful of expected matches kept missing them. The cost is that the server sees a full unpadded scan for that query, which tells it the filter was rare. Strict already scans everything, so nothing changes there. The result reports it as `strict_fallback`.

## What is slow

Strict and the rare-filter fallback score every block, so their time grows with the collection. Decoy scores up to $6p$ clusters in two rounds against Fast's $p$ to $2p$. [Latency and throughput](../performance/latency-and-throughput.md) has measured times per level.
