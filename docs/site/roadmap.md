# Roadmap

This page lists what is not built yet. Nothing here works today, and nothing here has a date. The order can change, and an item can be dropped. If you need one of these now, use what the "Today" line says.

Items are in two groups. "Next" is work that has started or is small and agreed. "Later" is work we want but have not started.

## Next

**Bucketing of filter value counts in the client.** The server sees how many fields a filter uses and how many distinct values each field has. The client does not yet round those counts up to a few sizes, so the exact counts leak. [What the server learns](security/what-the-server-learns.md) lists them as they are. Today: keep the number of values per field in mind when you design your fields.

## Later

### Privacy

**Hiding the record fetch after a search.** After a search, the client fetches the best candidates by id, so the server sees which records matched. Strict mode hides which clusters a query asks for, but not this step. Hiding it needs private retrieval, which we have not designed for Ombra. It would cost more time and bandwidth. Today: use random ids, so the ids reveal nothing by themselves.

**Hiding which cluster each record is in.** On insert, the client sends the cluster of each vector in the clear, next to its id. The server therefore holds the full split of ids into clusters. No setting hides this today. We want an option that does.

**Key rotation on the server.** `ombra-migrate --new-keys` rotates keys today: the client encrypts every vector again under a new key pair and seals every blob under a new master key, then switches an alias. That moves the whole collection through the client. We plan a server-side switch that keeps stored vectors seeded, so large collections can rotate without that transfer. It needs a new envelope version and an outside review first. Today: use `ombra-migrate --new-keys`, see [Migrating a collection](operations/migration.md).

**An external review of the cryptography.** No outside cryptographer has reviewed Ombra yet. One part needs it most: stored ciphertexts are seeded, and the noise used there depends on the secret key, unlike the textbook form. No attack is known. We plan a review of that step and of the rest of the design. Until then, treat the cryptography as unreviewed.

### Scale and operations

**Several nodes and a router.** One server runs on one machine, so a collection must fit there. We plan a router that sends each query to the nodes holding the clusters it needs and merges the encrypted results. Today: split your data across separate collections or servers in your own code.

**Other operations features.** Mutual TLS as an alternative to API keys, a Helm chart for Kubernetes, backup to object storage such as S3, and a scheduler that runs several queries in one pass over the stored data. Today: see [Backups](operations/backups.md) for how to back up the data directory.

**More platforms.** Windows client wheels and support for Blackwell GPUs. Today the client has no Windows build, and the server is built for Turing, Ampere, Ada and Hopper GPUs only.

### Features

**Range filters.** Filters work on integer fields. Ranges over dates or numbers need encrypted comparisons, which we have not built. Today: turn a range into buckets, for example a field whose value is the month, and filter on the bucket.

**A "precise" preset.** A preset with higher precision. The name exists, but the client and the server refuse it. Today: use the presets listed in [Presets](reference/presets.md).

**Plaintext-database mode.** For a corpus that any client may read, the server could keep vectors in the clear and hide only the query and the results. It would be faster. It does not exist: every vector is always encrypted.

**Automatic normalization.** The client would normalize vectors for you. Today: normalize them before you insert.

### Housekeeping

**Our own k-means.** The client uses faiss for clustering, in a child process, because faiss and OpenFHE cannot share a process on macOS. We may replace it with our own k-means. That removes a dependency and the child process, but it changes the clusters, so we would measure recall again first.
