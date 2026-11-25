# Deletes and compaction

A vector lives in one slot of each column of its block. The server cannot remove one slot, because it cannot read it. So a delete has two steps: the server marks the vector dead right away, and the client later rewrites the blocks without the dead slots.

## Delete and tombstones

`delete(ids)`: the client opens the vector's record blob to learn its field values, finds its cluster by the nearest center and lowers the cluster statistics ([filters](filters.md)). It tells the server to tombstone the ids (the server keeps a set of deleted ids) and deletes the record and document blobs. Unknown and already deleted ids are ignored, and the call returns how many it deleted.

At search time the server still scores the dead slot, but adds a plaintext penalty to it:

$$\text{penalty} = -\lambda\,(F + 1)$$

where $\lambda = {{ const("ombra.presets.FILTER_LAMBDA") }}$ is the filter weight and $F$ the number of filter fields (0 if none). The client's band is then at most $-1$, while a live vector that passes the filter has band $F$ ([filters](filters.md) explains bands). A tombstoned vector can never be a candidate. This is why $F_{max}$ keeps a spare unit of headroom. The server adds the penalty only to blocks with at least one tombstoned id. Example with $F = 2$: a vector with score 0.9 that matched both fields has slot ${{ (0.9 + 2 * const("ombra.presets.FILTER_LAMBDA")) | round(1) }}$; after the penalty of $-{{ const("ombra.presets.FILTER_LAMBDA") * 3 }}$ the client sees ${{ (0.9 + 2 * const("ombra.presets.FILTER_LAMBDA")-3 * const("ombra.presets.FILTER_LAMBDA")) | round(1) }}$, band $-1$, and drops it.

A delete frees nothing: ciphertexts keep their size, and searching a block costs the same with or without dead slots. A block whose slots are all dead is dropped by the server on its own. Anything else needs compaction.

## Compaction (repack)

Only the client can decrypt a block, so the client compacts. `repack` asks the server for the blocks with their counts of used and deleted slots and picks the clusters worth rewriting. For each one it downloads the blocks, decrypts every column, drops the dead slots, merges blocks that now fit together, encrypts the result and sends it back in a `ReplaceBlock` call. The server swaps the new block in for the old ones in one step, so searches keep working.

A cluster is rewritten when any of its blocks has

$$\frac{\text{deleted slots}}{\text{used slots}} \ge \tau$$

with $\tau = {{ const("ombra.repack.DEFAULT_THRESHOLD") }}$ by default (the `threshold` argument changes it). In a flagged cluster, blocks with live vectors are packed first-fit, largest first, into groups of at most $S$ live vectors ({{ preset("balanced", "batch_size") }} on Balanced, {{ preset("fast", "batch_size") }} on Fast). Each group becomes one new block, written if it holds a flagged block or merges more than one. Example on Balanced: live counts 5000, 2500 and 900 pack as 7500 (5000 + 2500) and 900, since $8400 > {{ preset("balanced", "batch_size") }}$. That is two blocks in place of three.

Repack moves data: every column of every rewritten block, $d + \sum_f m_f$ ciphertexts per block, is downloaded, decrypted, encrypted and uploaded. It needs the secret key, so it runs on the client. Run it when the deleted fraction is high, not after every delete. It returns a report with block counts and bytes before and after. Many small `insert_vectors` calls leave many half-empty blocks (each adds a new block per touched cluster); repack merges those too, but only in clusters that have a flagged block.

`repack` and `recluster` are methods on `Collection` in `ombra.collection`. The `Client` wrapper has `delete` but does not expose them yet.

## Re-clustering

The centers are fixed at creation. Over time the clusters stop matching the data, which costs recall at a given probe count. `recluster` reads every block with live vectors, decrypts it, and runs k-means again on the live vectors on your machine, in the same child process as at creation ([clusters](clusters.md)). It builds a new collection called `<name>.g<n>`, with the number of clusters you pass or the old count, using the same keys and no dead slots. The record and document blobs are copied byte for byte, because their encryption is tied to the base name and not to the generation.

Search keeps working on the old collection meanwhile. When `recluster` returns, switch your searches to the new collection and call `retire()` on the old one to drop it from the server. Until then the server stores about twice the data. It is the slowest operation (it decrypts and re-encrypts everything and copies every blob over gRPC), so use a maintenance window. Nothing triggers it automatically or tells you when the clusters have drifted.

!!! deep-dive "Why the statistics are rebuilt, not copied"
    The cluster statistics ([filters](filters.md)) count live vectors per cluster and per field value. After re-clustering every vector can sit in a different cluster, so the new collection builds fresh statistics from the new labels and seals them as an encrypted blob. The old collection's blob is left alone.
