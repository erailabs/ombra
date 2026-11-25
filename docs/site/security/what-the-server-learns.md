# What the server learns

What an honest-but-curious server can see, by search level. This follows the client code, not the design goals. The levels are Fast, Decoy and Strict. Each collection has a default, and a query can override it.

## In every mode

The server never sees vectors, queries, scores, filter values, document text, metadata values or the secret key. (A server that lies is in [Out of scope](out-of-scope.md).) It sees:

- **Vector ids**, in plaintext, on every insert, delete and returned score. Use random ids.
- **Cluster membership.** Each insert sends every vector's cluster id in the clear. The server holds the full partition of ids into clusters and their sizes. It does not know what a cluster is about.
- **Collection shape.** Preset, dimension, number of clusters, blocks and vectors (tombstoned ones included until compacted), and the filter layout: number of fields and values per field. Cardinalities are exact; bucketing them (for example by declaring a larger value count) is up to you. It does not see what a field means or its values.
- **Deletes**: the ids and when. A deleted vector stays as a tombstone, still scored and returned, until its block is dropped or repacked. The count of deleted vectors per block is visible. See [Deletes](../how-it-works/deletes.md).
- **Blob sizes and ids.** Every record and document blob is padded to one of four sizes ({{ const("ombra.blobstore.BUCKET_SIZES")[0] }}, {{ const("ombra.blobstore.BUCKET_SIZES")[1] }}, {{ const("ombra.blobstore.BUCKET_SIZES")[2] }} or {{ const("ombra.blobstore.BUCKET_SIZES")[3] }} bytes), so it sees the size class only. A vector's record and document use the vector's id. The per-cluster statistics are one sealed blob under a reserved id.
- **Query count and timing**: when each query arrives, how long each round takes, and how many bytes move.
- **Candidate ids after the search.** To re-rank, the client fetches the record blobs of its best `rerank_factor * k` candidates (3 times k by default, never fewer than k). The server sees those ids but not which end up in the final k, and cannot read the vectors. The ids are near neighbours of the query. A document fetched afterwards is one more blob request. This happens at every level, and because the server knows each id's cluster it shows which records were candidates and, from their number, k. The levels hide only which clusters a query requested.
    - `rerank=False` fetches no blob; hits carry approximate scores and no metadata.
    - A collection created with `private_retrieval=True` re-ranks with one encrypted call that shows only its scope (the requested clusters, all at Strict), the number of rows and message sizes, see [Private record retrieval](../how-it-works/private-retrieval.md). Hits carry no metadata, and reading a record by id reveals that id.
- **Public keys**: key tag, crypto context and evaluation keys, uploaded once per collection.
- **Network metadata**: client address, API key and request sizes.

Hidden in every mode: the query vector, scores, filter values, and which candidates are the real top k. Filtered and unfiltered queries have the same encrypted format, because unused filter entries carry a fixed value.

## Fast

Fast scores only the clusters closest to the query. The server also learns:

- **The clusters asked for**, a coarse hint at the query's topic, in rank order, best first.
- **The first-round size**: the square root of the cluster count rounded up, or `n_probe`.
- **Whether the query was filtered.** An unfiltered query normally takes one round and a filtered one exactly two. An unfiltered one takes more if the first returns fewer than k candidates.
- **Hints about the filter value.** A filtered query skips clusters the client's statistics say have no matches, so its clusters can point at the value.
- **A rare filter.** When expected selectivity is under 0.5%, the client scans every cluster in two rounds, split at the middle cluster id. The server sees the full scan and knows the filter matched very little.

## Decoy

Decoy does exactly two rounds. Each asks for a fixed number of clusters that depends only on the first-round size and the cluster count: twice the first-round size in round 1, then four times (or what is left) in round 2. Real clusters are mixed with decoys picked at random from clusters not yet asked, and the client shuffles each request. A cluster is never asked twice in one query. The server also learns:

- **Rounds and clusters per round**: always two, fixed counts, so it cannot tell filtered from unfiltered, or narrow from wide.
- **A set of clusters that contains the real ones.** Real and decoy look the same.
- **A full scan for a rare filter**, as in Fast. Its request sizes differ from a normal Decoy query, so it stands out.

Decoys protect one query at a time. Over many queries on a topic the real clusters repeat and the decoys do not, see [Out of scope](out-of-scope.md).

### Decoy groups (optional, off by default)

A collection created with `decoy_group_size` of 2 or more groups its clusters with a keyed permutation. A Decoy query asks for whole groups, and its extra groups are a keyed function of its real groups and the round number, so the same real groups always give the same request.

- **Hides** which cluster of a group was wanted. Repeating or intersecting similar searches narrows requests to the real groups at best, never to one cluster. The server does not know which clusters share a group.
- **Does not hide** the real groups, the fixed counts, the rare-filter full scan, the re-rank candidate ids, or that related queries ask for the same set. Slightly different queries can ask for different real groups, so many noisy variants of a topic can still narrow the set down.
- **Cost:** scoring work is unchanged, but the clusters filling a group are not the nearest ones, so recall can fall.

## Strict

Strict asks for every cluster in two rounds: the first half of the cluster ids, then the second half, in a fixed order independent of the query. Filtered, unfiltered and rare-filter queries look the same. The server learns nothing from the cluster choice. The candidate fetch after the search still shows which records were near the query (unless `rerank=False`). Strict scores every vector, so it is the slowest level. See [Clusters and search levels](../how-it-works/clusters.md).

## Side by side

| What the server sees | Fast | Decoy | Strict |
| --- | --- | --- | --- |
| Which clusters the query is closest to | yes, with rank order | yes, as a set that also holds decoys | no |
| Number of rounds | 1 unfiltered, 2 filtered | always 2 | always 2 |
| Whether a filter was used | yes, from the round count | no, unless the filter is rare | no |
| Rare filter (under 0.5%) | visible as a full scan | visible as a full scan | not visible |
| Record ids fetched for re-ranking | yes (none with `rerank=False` or private retrieval) | same | same |
| Vectors, queries, scores, filter values | no | no | no |

!!! deep-dive "Where each item comes from"
    The level logic is `run_search` in `client/ombra/search.py` (`run_round` pads Decoy rounds, `scan_all` splits a full scan with `strict_halves`, `_rerank` fetches record blobs). `decoy_round_sizes` and `ProbePlanner` are in `client/ombra/probe.py`. `server/grpc/src/service.cpp` answers clusters in request order and logs only counts, sizes and timings, never ids. The messages are in `client/ombra/resources/ombra.proto`.
