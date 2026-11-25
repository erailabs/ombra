# Filters

The server cannot read your metadata, so it cannot filter on it. Ombra moves the filter into the score. Each vector carries its filter values as extra encrypted columns, the query asks for them with a weight, and a vector that fails the filter lands in a different range of score values. The client decrypts the scores, reads the range and drops the failures. The server never learns which fields were filtered.

## The additive penalty

A collection can have $F$ filter fields. Field $f$ takes one of $m_f$ values, numbered $0$ to $m_f-1$. Each field becomes $m_f$ extra one-hot columns next to the $d$ vector columns: the column for value $v$ holds 1 for vectors whose field equals $v$ ([encoding](encoding.md)). The query grows the same way, to $d + \sum_f m_f$ entries: the normal $q_j$, then the weight $\lambda = {{ const("ombra.presets.FILTER_LAMBDA") }}$ at each allowed value of a filtered field and 0 at the others. A field you do not filter has $\lambda$ at every value. The server computes the same sum for every query ([encrypted search](encrypted-search.md)), so it cannot tell a filtered query by the shape of the work. One vector comes out as

$$\text{slot} = \underbrace{\sum_{j} q_j v_j}_{\text{score}} + \lambda c$$

where $c$ is the number of fields on which the vector matches (an unfiltered field always matches). A vector passes when $c = F$. Matching inside a field is OR, across fields AND. Fields and values are numbered integers; there is no text matching. Example: language (3 values) and year (2 values), filter language in {0, 2} and year = 1: the query extension is $[4, 0, 4,\ 0, 4]$, so language 2, year 1 gets $c = 2$ and language 1, year 1 gets $c = 1$.

## Bands

Because $\lambda$ is larger than the score range, each value of $c$ owns a band of width $\lambda$:

$$c = \left\lfloor \frac{\text{slot} + 2}{\lambda} \right\rfloor \qquad \text{score} = \text{slot}-\lambda c$$

The shift by 2 centers each band on $\lambda c$. A score of at most 1 in size leaves a margin of 1 on each side for CKKS noise. Slot 8.3 gives $c = 2$ and score $0.3$. The score kept is approximate; the final ranking uses the exact vectors from the record blobs.

A deleted vector stays in its block until the block is rewritten. The server adds $-\lambda(F + 1)$ to its slot, so its band is at most $-1$ and never equals $F$: it can never pass, with or without a filter ([Deletes and compaction](deletes.md)).

## Limits per preset

The decrypted slot must stay below $2^{q_0-\Delta-1}$ (first modulus $q_0$ bits, scale $\Delta$ bits). The client takes 3 bits less as a margin:

$$A = 2^{\,q_0-\Delta-4} \qquad F_{max} = \min\!\left(F_{cap},\ \left\lfloor \frac{A-1}{\lambda} \right\rfloor-1\right)$$

where $F_{cap}$ is the server's cap, {{ const("ombra.presets.MAX_FILTER_FIELDS") }}. $F$ fields add at most $\lambda F$, and the extra $-1$ leaves room for the tombstone penalty.

{% set fq = preset("fast", "first_mod_size") %}{% set fs = preset("fast", "scaling_mod_size") %}{% set bq = preset("balanced", "first_mod_size") %}{% set bs = preset("balanced", "scaling_mod_size") %}{% set lam = const("ombra.presets.FILTER_LAMBDA") %}Fast ($q_0 = {{ fq }}$, $\Delta = {{ fs }}$): $A = {{ 2 ** (fq-fs-4) }}$, so $F_{max} = {{ fmax("fast") }}$ after the cap. Balanced ($q_0 = {{ bq }}$, $\Delta = {{ bs }}$): $A = {{ 2 ** (bq-bs-4) }}$, so $F_{max} = {{ fmax("balanced") }}$. Balanced has the larger scale, so less room is left for the weight.

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

**Buckets (the default).** A range field has 64 values, called buckets. You give up to 63 cut points; bucket $b$ holds values from cut point $b-1$ up to, not including, cut point $b$. The client stores the cut points in an encrypted blob next to the cluster statistics (client format, version 1, under a reserved id). A record goes in its bucket, and its raw integer stays in its encrypted record. A range becomes the list of buckets it touches, queried as an equality filter. A range with both ends on cut points is exact. Otherwise the edge buckets also hold values outside the range, and the client drops those after reading the raw integer of each candidate it fetches for re-ranking. `ombra.ranges.quantile_edges` gives cut points with about equal records per bucket (best for skewed values); `equal_width_edges` gives equal widths. Example: cut points 10, 20, 30 give four buckets; the range 12 to 25 asks for buckets 1 and 2, and a record with value 11 comes back and is dropped.

**Two digits (prototype).** `{"kind": "two_digit"}` stores a value from 0 to 4095 in two 64-value fields, the coarse digit $v \div 64$ and the fine digit $v \bmod 64$. The query gives weight 2 to every coarse digit strictly inside the range, weight 1 to the two edge coarse digits, and weight 1 to the fine digits the edges need. A record in range scores 2 or 3 units for the field and one outside scores at most 1; with a range over several coarse digits the shared fine weights let a few outside records reach 2. The client accepts totals from $F$ to $F+m$ (with $m$ digit fields in the filter) and checks the raw value. The field counts as 3 units toward $F_{max}$.

**Fetch count.** Every search in a collection with range fields fetches the same number of record blobs, {{ const("ombra.presets.RANGE_FETCH_FACTOR") }} times the usual $3k$, whatever the filter, so the count does not reveal selectivity. If fewer candidates exist, the client fetches other ids it saw in the probed clusters and discards them, in random order. If fewer than $k$ verified matches remain, the result says `incomplete`.

**Without re-ranking.** `rerank=False` fetches no record, so the client cannot read raw values. It allows a range filter only when the band alone is exact: for buckets, every bound sits on a cut point or is open; for two digits, bounds sit on multiples of 64 and the range is the only filtered field. Any other range raises `ValueError`.

**What the server learns.** The same as for equality fields: the number of fields and their size. Every range field has 64 values (two of 64 for two digits), so it does not stand out. It sees the fixed fetch count and, in Fast, the round count. It does not see the bounds, cut points or values.

## Choosing clusters for a filter

With a filter, the best clusters by center score may hold no matches. The client keeps statistics, stored as an encrypted blob the server cannot read: per cluster the number of live vectors, and per field and value how many have that value. It updates them on insert and delete and rebuilds them when it re-clusters. It estimates the matches in cluster $i$ as

$$E_i = n_i \prod_{f \in \text{filtered}} \frac{n_{i,f,A_f}}{n_i}$$

where $n_i$ is the number of live vectors and $n_{i,f,A_f}$ the number whose field $f$ is in the allowed set $A_f$. The product treats fields as independent; when they are correlated the estimate is off, which hurts only the cluster choice, since every candidate is checked by its band.
Round 1 goes down the clusters in center-score order, skips clusters with $E_i = 0$ and adds clusters until the running sum of $E_i$ reaches $3k$, but never fewer than an unfiltered query would take. In Decoy the real part of round 1 is capped at $2p$ clusters ([clusters](clusters.md)).

## The extra round

A filtered query always makes a second round: the next clusters with $E_i > 0$, twice as many as round 1 used. It runs even if round 1 found enough matches, which keeps the round count the same for every filtered query at a level. There is no third round. If fewer than $k$ vectors passed after two rounds, the result says `incomplete` and is not padded.

Without statistics (a collection opened without its stats blob), a filtered query still makes two rounds but picks clusters by center score only: the first $p$, then the next $2p$. If the estimated selectivity, $\sum_i E_i / \sum_i n_i$, is under 0.5%, Fast and Decoy scan all clusters in two rounds with no decoys, and the server sees it ([Clusters](clusters.md), [what the server learns](../security/what-the-server-learns.md)). In Fast, an unfiltered query normally makes one round and a filtered query two, so the server can tell them apart. Decoy and Strict use two rounds for every query.

## What the server sees of the filter

Not field values, names or which values a query allows. It does see the number of filter fields and the number of values of each field, exactly, and a distinctive value count can say something about what a field holds. The client does not bucket the counts; declaring a larger count than you need so fields look alike is up to you.

!!! deep-dive "Why not stop at k matches in round 1"
    The first version stopped as soon as round 1 had $k$ matches. At around 1% selectivity this lost recall, because the matches found first were not the nearest. The planner now works from the statistics and always takes a second round. Its weak spot is the independence assumption.
