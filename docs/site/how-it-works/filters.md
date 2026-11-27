# Filters

The server cannot read your metadata, so it cannot filter on it. Ombra moves the filter into the score. Each vector carries its filter values as extra encrypted columns, the query asks for them with a weight, and a vector that fails the filter lands in a different range of score values. The client decrypts the scores, reads the range and drops the failures. The server never learns which fields were filtered.

## The additive penalty

A collection can have $F$ filter fields. Field $f$ takes one of $m_f$ values, numbered $0$ to $m_f-1$, and becomes $m_f$ one-hot columns next to the $d$ vector columns ([encoding](encoding.md)). The query grows the same way, to $d + \sum_f m_f$ entries: the normal $q_j$, then the weight $\lambda = {{ const("ombra.presets.FILTER_LAMBDA") }}$ at each allowed value of a filtered field and 0 at the others. A field you do not filter has $\lambda$ at every value. The server computes the same sum for every query ([encrypted search](encrypted-search.md)), so the work looks the same. One vector comes out as

$$\text{slot} = \underbrace{\sum_{j} q_j v_j}_{\text{score}} + \lambda c$$

where $c$ is the number of fields on which the vector matches (an unfiltered field always matches). A vector passes when $c = F$. Matching inside a field is OR, across fields AND. Fields and values are numbered integers; there is no text matching.

## Bands

Because $\lambda$ is larger than the score range, each value of $c$ owns a band of width $\lambda$:

$$c = \left\lfloor \frac{\text{slot} + 2}{\lambda} \right\rfloor \qquad \text{score} = \text{slot}-\lambda c$$

A score of at most 1 in size leaves a margin of 1 on each side for CKKS noise. The score kept is approximate; the final ranking uses the exact vectors from the record blobs.

A deleted vector stays in its block until the block is rewritten. The server adds $-\lambda(F + 1)$ to its slot, so its band is at most $-1$ and never equals $F$: it can never pass ([Deletes and compaction](deletes.md)).

## Limits per preset

The decrypted slot must stay below $2^{q_0-\Delta-1}$ (first modulus $q_0$ bits, scale $\Delta$ bits). The client keeps a margin of 3 bits:

$$A = 2^{\,q_0-\Delta-4} \qquad F_{max} = \min\!\left(F_{cap},\ \left\lfloor \frac{A-1}{\lambda} \right\rfloor-1\right)$$

where $F_{cap}$ is the server's cap, {{ const("ombra.presets.MAX_FILTER_FIELDS") }}. The extra $-1$ leaves room for the tombstone penalty. The result is $F_{max} = {{ fmax("fast") }}$ for Fast and ${{ fmax("balanced") }}$ for Balanced, which has the larger scale and so less room for the weight.

Creating a collection with more than $F_{max}$ fields fails. A field has 1 to {{ const("ombra.presets.MAX_FILTER_CARDINALITY") }} values, and $d + \sum_f m_f$ must be at most half the ring dimension ({{ preset("fast", "ring_dim") // 2 }} for Fast, {{ preset("balanced", "ring_dim") // 2 }} for Balanced). A field with many distinct values, such as a user id, does not fit: bucket it into at most {{ const("ombra.presets.MAX_FILTER_CARDINALITY") }} values or keep it out of the filter.

## Range fields

A filter such as `year >= 2020` is still a sum of one-hot columns; the server never learns that a field is a range. Declare range fields at creation and put the raw integer in each record's metadata:

```python
client.create_collection(
    "shop", vectors, ids, key_file=key_file, passphrase=passphrase,
    metadata=[{"year": 2019, "price": 120}, ...],
    range_fields={"year": [1995, 2000, 2010, 2020], "price": {"kind": "two_digit"}},
)
hits = client.search("shop", query, filter={"year": {"gte": 2015, "lt": 2022}})
```

A bound is `gte`, `gt`, `lte` or `lt`, alone or two together. A plain integer means equality. Ranges and equality fields combine with AND.

**Buckets (the default).** A range field has 64 buckets. You give up to 63 cut points; bucket $b$ holds values from cut point $b-1$ up to, not including, cut point $b$. The client stores the cut points in an encrypted blob next to the cluster statistics (client format, version 1). A range becomes the list of buckets it touches, queried as an equality filter. It is exact when both ends sit on cut points. Otherwise the edge buckets also hold outside values, and the client drops them after reading the raw integer of each candidate it fetches for re-ranking. `ombra.ranges.quantile_edges` gives cut points with about equal records per bucket (best for skewed values); `equal_width_edges` gives equal widths.

**Two digits (prototype).** `{"kind": "two_digit"}` stores a value from 0 to 4095 in two 64-value fields, the coarse digit $v \div 64$ and the fine digit $v \bmod 64$. The client checks the raw value of every match. The field counts as 3 units toward $F_{max}$.

**Fetch count.** Every search in a collection with range fields fetches the same number of record blobs, {{ const("ombra.presets.RANGE_FETCH_FACTOR") }} times the usual $3k$, whatever the filter, so the count does not reveal selectivity. If fewer candidates exist, the client fetches other ids it saw in the probed clusters and discards them. If fewer than $k$ verified matches remain, the result says `incomplete`.

**Without re-ranking.** `rerank=False` fetches no record, so the client cannot read raw values. It allows a range filter only when the band alone is exact: for buckets, every bound sits on a cut point or is open; for two digits, bounds sit on multiples of 64 and the range is the only filtered field. Any other range raises `ValueError`.

**What the server learns.** The same as for equality fields. Every range field has 64 values (two of 64 for two digits), so it does not stand out. It sees the fixed fetch count, not the bounds, cut points or values.

## Choosing clusters for a filter

With a filter, the best clusters by center score may hold no matches. The client keeps statistics, stored as an encrypted blob the server cannot read: per cluster the number of live vectors, and per field and value how many have that value. It updates them on insert and delete and rebuilds them when it re-clusters. It estimates the matches in cluster $i$ as

$$E_i = n_i \prod_{f \in \text{filtered}} \frac{n_{i,f,A_f}}{n_i}$$

where $n_{i,f,A_f}$ is the number of live vectors whose field $f$ is in the allowed set $A_f$. The product treats fields as independent; when they are correlated the estimate is off, which hurts only the cluster choice, since every candidate is checked by its band.

Round 1 goes down the clusters in center-score order, skips those with $E_i = 0$ and stops when the running sum of $E_i$ reaches $3k$, taking at least as many as an unfiltered query would (in Decoy the real part is capped at $2p$, see [clusters](clusters.md)). A filtered query always makes a second round of the next clusters with $E_i > 0$, twice as many as round 1 used, even if round 1 found enough matches. There is no third round. If fewer than $k$ vectors passed, the result says `incomplete` and is not padded.

Without statistics (a collection opened without its stats blob), a filtered query still makes two rounds but picks clusters by center score only: the first $p$, then the next $2p$. If the estimated selectivity, $\sum_i E_i / \sum_i n_i$, is under 0.5%, Fast and Decoy scan all clusters with no decoys, and the server sees it. In Fast, an unfiltered query normally makes one round and a filtered query two, so the server can tell them apart. Decoy and Strict use two rounds for every query.

## What the server sees of the filter

Not field values, names or allowed values. It sees the number of filter fields and the number of values of each, and a distinctive count can hint at what a field holds. The client does not bucket the counts; declaring a larger count so fields look alike is up to you ([what the server learns](../security/what-the-server-learns.md)).
