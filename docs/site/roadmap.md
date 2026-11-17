# Roadmap

Not built yet, in no fixed order and with no dates.

## Next

**Bucketing of filter value counts.** The server sees how many fields a filter uses and how many distinct values each field has. The client does not round those counts yet, so the exact counts leak ([what the server learns](security/what-the-server-learns.md)). For now, keep the number of values per field in mind when you design your fields.

## Later

### Privacy

**Hiding the record fetch.** After a search, the client fetches the best candidates by id, so the server sees which records matched. Strict mode does not hide this. It needs private retrieval, which costs time and bandwidth and is not designed yet. For now, use random ids.

**Hiding which cluster each record is in.** On insert, the client sends each vector's cluster in the clear, so the server knows the split of ids into clusters. No setting hides this yet.

**Key rotation on the server.** `ombra-migrate --new-keys` rotates keys by moving the whole collection through the client ([Migrating a collection](operations/migration.md)). A server-side switch would avoid that transfer for large collections. It needs a new envelope version and an outside review.

**An external review of the cryptography.** No outside cryptographer has reviewed Ombra. The part that needs it most: stored ciphertexts are seeded, and the noise there depends on the secret key, unlike the textbook form. No attack is known. Until the review, treat the cryptography as unreviewed.

### Scale and operations

**More from the router.** The [router](operations/routing.md) works, with limits: one copy of each cluster, no moving clusters between nodes, no `/metrics` or REST port on the router, and no alias-limited keys through it. Replicas, rebalancing and router metrics are planned.

**Other operations features.** Mutual TLS as an alternative to API keys, a Helm chart, backup to object storage such as S3, and a scheduler that runs several queries in one pass. For backups now, see [Backups](operations/backups.md).

**More platforms.** Windows client wheels and Blackwell GPUs. The server is built for Turing, Ampere, Ada and Hopper only.

### Features

**A "precise" preset.** The name exists, but the client and the server refuse it. Use the presets in [Presets](reference/presets.md).

**Plaintext-database mode.** For a corpus any client may read, the server could keep vectors in the clear and hide only the query and results. It would be faster. Today every vector is encrypted.

**Automatic normalization.** For now, normalize vectors before you insert them.

### Housekeeping

**Our own k-means.** The client runs faiss in a child process, because faiss and OpenFHE cannot share a process on macOS. Our own k-means would drop that dependency, but it changes the clusters, so we would measure recall again first.
