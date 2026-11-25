# Accuracy

Encrypted search returns the same top results as a search on plaintext vectors. Encrypted scores carry a small error, too small to change the ranking. What loses recall is the clustering that keeps search fast.

## The measured error

CKKS is approximate: a decrypted score is the true score plus a small error from encoding, encryption and every multiplication, relinearization and rescale (see [CKKS](../concepts/ckks.md)). It does not grow with the number of vectors, because each slot is computed on its own. It depends on the preset and on the dimension $d$, since a score sums $d$ products. We measured only the total. We ran the full encrypted pipeline on three public embedding sets (d = 384, 768 and 1536) with 100,000 vectors and 1,000 queries each, and compared every decrypted score with the exact one. Largest absolute error:

| Preset | d = 384 | d = 768 | d = 1536 |
| --- | --- | --- | --- |
| `balanced` | $9.8 \times 10^{-9}$ | $1.9 \times 10^{-8}$ | $2.8 \times 10^{-8}$ |
| `fast` | $5.1 \times 10^{-6}$ | $1.0 \times 10^{-5}$ | $1.7 \times 10^{-5}$ |

Cosine scores lie between $-1$ and $1$, so `balanced` is accurate to about 8 decimal places and `fast` to about 5.

## Why encryption costs no recall

Two scores with error at most $\varepsilon$ can swap only when they differ by less than $2\varepsilon$, about $3.4 \times 10^{-5}$ for `fast`. The measurements agree: on `balanced`, encrypted recall@10 equalled plaintext recall at all 22 settings on all three datasets. On `fast` the worst gap was 0.0004, against a tolerance of 0.005. Full-scan recall was 0.9999 to 1.0000.

Client re-ranking removes even that. The client fetches the encrypted records of the best $3k$ candidates (`rerank_factor = 3`), decrypts the exact vectors, recomputes the scores in full precision and sorts again. Only the candidate list then depends on the noisy scores. With re-ranking, full-scan recall was 1.000 on all three datasets at `fast` ([documents and blobs](documents.md)). Code: `_rerank` in [`client/ombra/search.py`](https://github.com/erailabs/ombra/blob/main/client/ombra/search.py).

**Without re-ranking.** `search(..., rerank=False)` ranks hits by their decrypted approximate scores. At Strict on 20,000 vectors (k = 10, wiki-simple, d = 384 and 768), recall@10 was within 0.0001 of recall with re-ranking. At the Fast level on d = 384 the two were equal (0.9764 at `fast`, 1.0000 at `balanced`). d = 1536 is not measured. Check your own data with `ombra-recall --no-rerank`. Re-ranking cannot bring back a vector missing from the candidate list.

## Where recall is lost: clustering

A true neighbour in an unprobed cluster is missed, identically for encrypted and plaintext search ([Clusters and search levels](clusters.md)). Recall@10 on 100,000 vectors of d = 1536 (OpenAI ada-002), 1,000 queries:

| nlist (clusters) | nprobe = 1 | nprobe = 10 | nprobe = 100 |
| --- | --- | --- | --- |
| 100 | 0.6941 | 0.9590 | 1.0000 |
| 316 | 0.6342 | 0.9263 | 0.9953 |
| 1000 | 0.5449 | 0.8797 | 0.9860 |

With 100 clusters, one probe covers about 1.13% of the database and ten cover about 10.9% (from an earlier 100-query run). More clusters make each probe cheaper but need more probes. Encrypted and plaintext recall were equal in every cell.

Filters add their own loss. In the first design we measured (arXiv abstracts, 40,000 vectors, 20 clusters), a filter matching about 1% of vectors lost 0.19 to 0.23 recall, plaintext included. The search now widens to more clusters when a filter is selective; in a plaintext simulation, recall at about 1% selectivity was at least 0.991. Not re-measured on the encrypted path ([Filters](filters.md)).

**Not measured.** Filtered recall on the encrypted path was measured on `fast` at d = 384 only, with 10,000 and 40,000 vectors. We have no split of the error by source, no figures for other dimensions or sizes, and none for error after many updates.

Code: [`core/kernels/column_search.cu`](https://github.com/erailabs/ombra/blob/main/core/kernels/column_search.cu), [`client/ombra/recall.py`](https://github.com/erailabs/ombra/blob/main/client/ombra/recall.py). What the server sees: [what the server learns](../security/what-the-server-learns.md).
