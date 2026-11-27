# Roadmap

Not built yet, in no fixed order and with no dates.

## Next

**Bucketing of filter value counts.** The server sees how many fields a filter uses and how many values each has ([what the server learns](security/what-the-server-learns.md)). The client does not round those counts yet. Keep the number of values per field in mind when you design fields.

## Later

### Privacy

**Private retrieval of metadata and documents.** [Private record retrieval](how-it-works/private-retrieval.md) hides which candidates a search re-ranks, but its hits carry no metadata, and reading the metadata or document of a hit is a fetch by id that the server sees. A private call for blobs, one call per blob size class with always k rows per class, is planned. For now, use random ids.

**Private retrieval on GPUs.** The products of the query with the stored rows and the second-dimension product run on the GPU and match OpenFHE byte for byte. Query expansion, now the largest remaining step, still runs on the CPU; moving it is planned, checked against OpenFHE the same way.

**Hiding which cluster each record is in.** The client sends each vector's cluster in the clear on insert, so the server knows the split of ids into clusters. No setting hides this yet, private retrieval included.

**Key rotation on the server.** `ombra-migrate --new-keys` rotates keys by moving the whole collection through the client ([Migrating a collection](operations/migration.md)). A server-side switch would avoid that transfer. It needs a new envelope version and an outside review.

**An external review of the cryptography.** No outside cryptographer has reviewed Ombra. The part that needs it most: stored ciphertexts are seeded, and their noise depends on the secret key, unlike the textbook form. No attack is known. Until the review, treat the cryptography as unreviewed.

### Scale and operations

**More from the router.** The [router](operations/routing.md) has one copy of each cluster, no moving clusters between nodes, no `/metrics` or REST port, and no alias-limited keys. Replicas, rebalancing and router metrics are planned.

**Other operations features.** Mutual TLS as an alternative to API keys, a Helm chart, backup to object storage such as S3, and a scheduler that runs several queries in one pass. For backups now, see [Backups](operations/backups.md).

**More platforms.** Windows client wheels and Blackwell GPUs. The server is built for Turing, Ampere, Ada and Hopper only.

### Features

**A "precise" preset.** The name exists, but the client and server refuse it. Use the presets in [Presets](reference/presets.md).

**Plaintext-database mode.** For a corpus any client may read, the server could keep vectors in the clear and hide only the query and results. Today every vector is encrypted.

**Automatic normalization.** For now, normalize vectors before you insert them.

### Housekeeping

**Our own k-means.** The client runs faiss in a child process, because faiss and OpenFHE cannot share a process on macOS. Our own k-means would drop that dependency, but it changes the clusters, so we would measure recall again first.
