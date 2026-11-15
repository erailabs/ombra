# Range filters: option A (buckets) against option D (two digits), Fast preset (decision 0030)

- **Command:** `python core/bench/range_filters.py --preset fast --queries 30` (local mode, CPU, `Client.local`; run on a shared 12-core machine, so latencies are noisy). Tables made by `core/bench/range_filters_report.py`. Raw numbers: `2026-11-15-range-filters.json`.
- **Data:** banking77 train utterances (CC-BY-4.0), 10,003 vectors, d = 384, MiniLM embeddings, 6 clusters, preset `fast`, 30 queries (random test utterances) per cell, k = 10, re-ranking factor 3, fetch factor 4 (fixed 120 record fetches per query).
- **Synthetic attribute v in 0..4095** (the same value in three fields of one collection, so the methods see the same vectors and queries): uniform U{0..4095}; skewed = min(4095, round(Exponential(mean 400))), which has many ties at the low end (about 63% of values below 512).
- **Methods:** A quantile (64 buckets, cut points at value quantiles), A equal-width (64 buckets of 64 values), D two digits (coarse = v // 64, fine = v % 64, weights 2/1/1 as in decision 0030).
- **Ranges:** random windows of the sorted values with a target share of 1%, 10%, 50% of the vectors (actual share in the table); the same windows for the three methods. Ground truth: brute-force exact filtered top-10 (float64). Recall is the share of the true top-10 (or all matches, if fewer) returned.
- **Columns:** "fewer than k" is the share of queries that return fewer than 10 verified results (`incomplete`). "Edge FP (data)" is computed from the data: the share of records that the encrypted test lets through that lie outside the range (all records, no probing). "Edge FP (fetched)" is the share of fetched candidates the client check dropped. "Candidates" is the mean number of records that passed the decoder per query (before the client check). "Fetched" is the number of record blobs fetched per query (padded with discarded records, fixed). Latency is the median search call, with the key and data already on disk; it includes record fetches.

Preset fast, 30 queries per cell, 10003 vectors.

### skewed

| share | actual | method | recall@10 | fewer than k | edge FP (data) | edge FP (fetched) | candidates | fetched | latency ms (median) |
|---|---|---|---|---|---|---|---|---|---|
| 1% | 1.1% | A quantile | 1.000 | 0% | 53% | 54% | 265 | 120 | 3874 |
| 1% | 1.1% | A equal-width | 0.917 | 33% | 85% | 85% | 1051 | 120 | 6030 |
| 1% | 1.1% | D two digits | 1.000 | 0% | 7% | 7% | 129 | 120 | 5714 |
| 10% | 10.1% | A quantile | 1.000 | 0% | 10% | 9% | 1127 | 120 | 2949 |
| 10% | 10.1% | A equal-width | 1.000 | 0% | 40% | 41% | 1854 | 120 | 2432 |
| 10% | 10.1% | D two digits | 1.000 | 0% | 28% | 28% | 1510 | 120 | 2415 |
| 50% | 50.1% | A quantile | 1.000 | 0% | 3% | 3% | 5164 | 120 | 3038 |
| 50% | 50.1% | A equal-width | 1.000 | 0% | 16% | 16% | 6007 | 120 | 7701 |
| 50% | 50.1% | D two digits | 1.000 | 0% | 10% | 11% | 5618 | 120 | 5276 |

### uniform

| share | actual | method | recall@10 | fewer than k | edge FP (data) | edge FP (fetched) | candidates | fetched | latency ms (median) |
|---|---|---|---|---|---|---|---|---|---|
| 1% | 1.0% | A quantile | 1.000 | 0% | 61% | 60% | 281 | 120 | 2922 |
| 1% | 1.0% | A equal-width | 1.000 | 0% | 57% | 57% | 263 | 120 | 2079 |
| 1% | 1.0% | D two digits | 1.000 | 0% | 36% | 36% | 179 | 120 | 1815 |
| 10% | 10.0% | A quantile | 1.000 | 0% | 11% | 11% | 1136 | 120 | 2145 |
| 10% | 10.0% | A equal-width | 1.000 | 0% | 13% | 12% | 1156 | 120 | 1946 |
| 10% | 10.0% | D two digits | 1.000 | 0% | 8% | 7% | 1088 | 120 | 1959 |
| 50% | 50.0% | A quantile | 1.000 | 0% | 3% | 3% | 5157 | 120 | 2990 |
| 50% | 50.0% | A equal-width | 1.000 | 0% | 3% | 3% | 5153 | 120 | 5550 |
| 50% | 50.0% | D two digits | 1.000 | 0% | 2% | 3% | 5129 | 120 | 6712 |


## Reading the numbers

- Exactness: after the client check no query returned a record outside its range (the unit tests assert the same on 4,000 random records for both encodings, with and without an equality field).
- Recall is 1.000 in 17 of 18 cells. This dataset is small (6 clusters, two probe rounds scan most of it), so the encodings are not stressed by pruning. The one loss is A equal-width on the skewed values at 1%: recall 0.917, 33% of queries return fewer than k. Equal-width buckets put most records into a few low buckets, so a 1% range sits inside one crowded bucket (85% false positives, 1,051 candidates) and the 120 fetched candidates, ranked by approximate score, hold fewer than 10 true matches for some queries.
- The fixed fetch count of 120 (4 x 3 x k) was enough everywhere except that case. D at 1% on skewed data has only 7% false positives because the value range is narrow and the coarse digit is selective there.
- D has fewer false positives than A at uniform 1% (36% against 57% to 61%), because its edge coarse digit matches only the fine values the range needs, but more at skewed 10% and 50% (28% and 10%, against 9% and 3% for quantile A). The shared fine weights let records in the other edge's coarse digit reach 2 units, as the review note says.
- Quantile buckets keep the false-positive rate within 3% to 11% for ranges of 10% and 50% on both distributions. At 1% every method has 36% to 61% false positives (a 1% range is below one bucket's width, 1.6%), and fetching 120 records covers it.
- Latency: A and D are the same within the noise of the shared machine (2 to 8 s, dominated by the CPU search over 640 columns, not by the encoding). The server work is identical by construction.
- Not run: the Balanced preset (it needs a second collection build and a longer CPU run; the encodings do not depend on the preset, only F_max does: 14 on Balanced against 16 on Fast, and D takes 3 units). Larger datasets and queries tied to the filter are also not measured. Per cell, 30 queries give a recall standard error of about 0.02 or less except in the one cell with a loss.

## Recommendation: A, with quantile buckets

Build A as the supported range filter and keep D as an optional prototype, not a default.

1. **Quantile A is never worse than D by more than noise, and better where it matters.** It has 100% recall and no short answers in all six cells, false positives of 3% to 11% at 10% and 50%, and the lowest crowding on skewed data. D matches it only at uniform 1% and skewed 1%.
2. **A is simpler to decode and costs fewer field units.** An A field is an ordinary equality field of 64 values: the decoder stays at $c = F$, and a field costs 1 unit toward F_max. D needs the decoder to accept a total from $F$ to $F+m$, which lets a record that fails an equality field and scores 3 on a digit pair look like a match (the client check removes it, but it can crowd the fetched set when several equality fields are combined). It also costs 2 fields of 64 columns and 3 units toward F_max, which is 1 unit more per range than A on Balanced (F_max 14).
3. **A has no pathological case when the cut points follow the data.** Its one failure here is equal-width buckets on skewed values. The client should default to quantile cut points computed from the data at create time (`ombra.ranges.quantile_edges`) and say so in the docs. D's coarse digit is fixed to equal widths of 64 values, so it cannot adapt to a skew, and it limits the domain to 0..4095.
4. **D's one advantage** is exact reach over a domain of 4,096 values with fixed cut points and no data pass. If exact large domains are needed, decision 0030's option C is still the right follow-up, since D's edge false positives are removed only by the client check.

Open items for the owner: whether to set the fetch factor above 4 (the cost is record fetches, which are fixed per query), and whether to run the Balanced preset and a larger corpus before accepting.
