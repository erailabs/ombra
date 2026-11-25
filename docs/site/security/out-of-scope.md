# Out of scope

## A server that lies

The threat model is an honest-but-curious server. A malicious server can do any of the following, and the client will not notice:

- Return wrong or made-up scores (scores are ciphertexts without an authenticity tag).
- Leave clusters out of a search, or return fewer results than exist.
- Return an older version of a record or document blob. Each blob is authenticated against its collection, kind and id, so a blob moved to another id fails to open, but a stale copy of the right id is accepted.
- Refuse to answer, or delete data. Back up your data, see [Backups](../operations/backups.md).

The final ranking is exact because the client re-scores the top candidates with the real vectors. That corrects CKKS noise. It does not stop a server from hiding the best match.

## Access patterns and metadata

Encryption hides values, not behavior. The server sees which ids you insert, which clusters you ask for, which record blobs you fetch and when ([What the server learns](what-the-server-learns.md)).

- Vector ids are plaintext. Use random identifiers, not email addresses or file names.
- Sizes: vectors, clusters, vectors per cluster, filter field count and values per field, and the size class of each blob.
- The number and timing of queries, and the candidate ids the client fetches after each search.

Strict mode removes the cluster choice from this list, not the blob fetches.

## Side channels

Timing, cache, power and memory-access side channels, on the server or the client, are neither tested nor defended against. The GPU kernels are not constant time. Anyone who can run code next to the secret key, or measure the client's decryption, is outside the model. The server sees how long each round takes, and in Fast and Decoy that depends on which clusters were asked.

## Repeated queries

Decoys hide the real clusters of one query, and the client shuffles each request. A server that watches many queries can compare them: the real clusters of related queries repeat while the decoys change, so the real ones stand out over time. We have not measured how fast. Only Strict avoids this, by scanning every cluster. Optional decoy groups (off by default, see [What the server learns](what-the-server-learns.md)) hide which cluster inside a group was wanted, but an observer can still narrow many varied queries down to the real groups.

## Compromised clients and keys

- Whoever has the key file and passphrase can decrypt everything. There is no per-user access control inside a collection.
- Losing the key file means losing the data. Nothing on the server can recover it.
- An API key limits who can send requests and what they can do. Encryption protects the data.
- Local mode runs the server code in a child process with no trust boundary to your machine. It is for trying Ombra out, not for hiding data from the machine it runs on.
