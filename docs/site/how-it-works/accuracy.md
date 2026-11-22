# Accuracy

Encrypted search returns the same top results as a search on plaintext vectors. Encrypted scores carry a small error, but it is too small to change the ranking. What does lose recall is the clustering that keeps search fast.

## Where noise comes from

CKKS is approximate: a decrypted score is the true score plus a small error. See the [CKKS page](../concepts/ckks.md) for the scheme.

Encoding turns each real number $x$ into the integer $\mathrm{round}(x \cdot \Delta)$, where $\Delta = 2^{s}$ is the scale and $s$ is the scale size in bits. That rounding is the first error. Encryption adds fresh random noise, and every multiplication, relinearization and rescale adds a little more. The error does not grow with the number of vectors, because each slot is computed on its own. It depends on the preset and on the dimension $d$, since a score sums $d$ products.

For example, the `fast` preset uses $s = {{ preset("fast", "scaling_mod_size") }}$ bits, so $\Delta = 2^{ {{ preset("fast", "scaling_mod_size") }} } = 1\,099\,511\,627\,776$. Encoding the value $0.0123$ stores the integer $\mathrm{round}(0.0123 \cdot \Delta) = 13\,523\,993\,022$, and decoding divides by $\Delta$ again. Rounding alone costs at most $0.5 / \Delta \approx 4.5 \times 10^{-13}$ per number. The error in a decrypted score is far larger, about $10^{-5}$ for `fast`, so the other sources dominate. We measured only the total, not each source.

## The measured error

We ran the full encrypted pipeline on three public embedding sets (d = 384, 768 and 1536) with 100,000 vectors and 1,000 queries each, and compared every decrypted score with the exact one. The largest absolute error we saw:

| Preset | Largest score error | Where it comes from |
| --- | --- | --- |
| `balanced` | $2.8 \times 10^{-8}$ (d = 1536); $9.8 \times 10^{-9}$ at d = 384, $1.9 \times 10^{-8}$ at d = 768 | recall check on the `balanced` preset |
| `fast` | $1.7 \times 10^{-5}$ (d = 1536); $5.1 \times 10^{-6}$ at d = 384, $1.0 \times 10^{-5}$ at d = 768 | recall check on the `fast` preset |

Cosine scores of unit vectors lie between $-1$ and $1$, so `balanced` is accurate to about 8 decimal places and `fast` to about 5. The `fast` preset has a smaller ring and a smaller scale, which makes it cheaper. The price is an error up to about 600 times larger (at d = 1536, $1.74 \times 10^{-5}$ against $2.77 \times 10^{-8}$).

## Why encryption costs no recall

A wrong ranking needs two vectors whose true scores are closer than the error. If each score is off by at most $\varepsilon$, two scores $a > b$ can swap order only when

$$a{-}b < 2\varepsilon .$$

Take $\varepsilon = 1.7 \times 10^{-5}$, the largest `fast` error above. Scores of $0.8123$ and $0.8122$ differ by $10^{-4}$, more than $2\varepsilon = 3.4 \times 10^{-5}$, so their order holds. Only pairs closer than $3.4 \times 10^{-5}$ can swap, and that changes the top 10 only if the tie sits at its edge.

The measurements agree. On `balanced`, encrypted recall@10 equalled plaintext recall at all 22 settings on all three datasets (gap 0.0000). On `fast` the worst gap was 0.0004, against a tolerance of 0.005. Full-scan recall was 0.9999 to 1.0000, and on the d = 768 set one query in 1,000 had a top 10 that differed from the plaintext top 10.

Client re-ranking removes even that. The server returns candidates ranked by encrypted score. The client fetches the encrypted record of each of the best $3k$ candidates, decrypts the exact vectors, recomputes the scores in full precision and sorts again. The default is `rerank_factor = 3`, so a search for $k = 10$ re-ranks 30 candidates. The final order no longer depends on the noisy scores, only the candidate list does. With re-ranking of 30 candidates, full-scan recall was 1.000 on all three datasets at `fast`. The record blobs it reads are described on the [documents and blobs](documents.md) page, and the code is `_rerank` in [`client/ombra/search.py`](https://github.com/erailabs/ombra/blob/main/client/ombra/search.py).

**Without re-ranking.** `search(..., rerank=False)` skips the fetch, so the hits are ranked by their decrypted approximate scores. At Strict on 20,000 vectors and 1,000 queries (k = 10), recall@10 without re-ranking was within 0.0001 of recall@10 with it:

| Dataset | Preset | With re-ranking | Without |
| --- | --- | --- | --- |
| wiki-simple, MiniLM, d = 384 | `fast` | 1.0000 | 0.9999 |
| wiki-simple, MiniLM, d = 384 | `balanced` | 1.0000 | 1.0000 |
| wiki-simple, Cohere, d = 768 | `fast` | 1.0000 | 0.9999 |
| wiki-simple, Cohere, d = 768 | `balanced` | 1.0000 | 1.0000 |

At the Fast level on d = 384 the two were equal (0.9764 at `fast`, 1.0000 at `balanced`). The d = 1536 dataset is not measured yet. Run the same check on your own data with `ombra-recall --no-rerank`.

Re-ranking cannot bring back a vector missing from the candidate list. On `balanced` it changed nothing in our run.

## Where recall is lost: clustering

Ombra groups vectors into clusters and searches only the nearest few. A true neighbour in a cluster you did not probe is missed. The loss is identical for encrypted and plaintext search, so encryption is not its cause. [Clusters and search levels](clusters.md) explains how the probe count is chosen.

Recall@10 on 100,000 vectors of d = 1536 (OpenAI ada-002 embeddings), 1,000 queries, with `nlist` clusters and `nprobe` clusters searched:

| nlist | nprobe = 1 | nprobe = 10 | nprobe = 100 |
| --- | --- | --- | --- |
| 100 | 0.6941 | 0.9590 | 1.0000 |
| 316 | 0.6342 | 0.9263 | 0.9953 |
| 1000 | 0.5449 | 0.8797 | 0.9860 |

With 100 clusters and one probed, a query looks at about 1.13% of the database and finds about 69% of the true top 10. Probing 10 clusters searches about 10.9% of the data and finds 96%. (The shares searched are from an earlier 100-query run on 1536-dimensional data, not the 1,000-query run.) More clusters make each probe cheaper but need more probes for the same recall. Encrypted and plaintext recall were equal in every cell.

Filters add their own loss. In the first design we measured on real text (arXiv abstracts, 40,000 vectors, 20 clusters), a filter matching about 1% of the vectors lost 0.19 to 0.23 recall, and the plaintext run with the same clusters lost exactly the same. The search now widens to more clusters when a filter is selective. In a plaintext simulation of that change, recall at about 1% selectivity was at least 0.991 on both test sets. We have not re-measured it on the encrypted path. [Filters](filters.md) covers the details.

!!! deep-dive "What we have not measured"
    Error figures cover the `balanced` and `fast` presets at d = 384, 768 and 1536 with 100,000 vectors. Filtered recall on the encrypted path was measured on `fast` at d = 384 only, with 10,000 and 40,000 vectors. We have no split of the error by source, no figures for other dimensions or sizes, and no measurement of how error behaves after many updates to a collection.

The score arithmetic is in [`core/kernels/column_search.cu`](https://github.com/erailabs/ombra/blob/main/core/kernels/column_search.cu). The tool that compares encrypted with plaintext recall is [`client/ombra/recall.py`](https://github.com/erailabs/ombra/blob/main/client/ombra/recall.py). What the server sees during a search is on [what the server learns](../security/what-the-server-learns.md).
