# Several nodes and a router

One `ombra_server` serves one data directory. To hold more clusters than one machine can, run several nodes behind `ombra_router`. Clients use the same API, and nodes are plain `ombra_server` processes. The router never decrypts anything and holds no HE key. It reads the placement file, splits each call by cluster, and passes encrypted answers on as bytes.

## How it works

Each cluster lives on exactly one node: the one with the highest random weight for (cluster number, node id), also called rendezvous hashing.

- The owner depends only on the cluster number and the node ids, never on vectors, centers or cluster sizes.
- Adding a node to n others moves about 1 in n+1 clusters, all to the new node. Removing a node moves only its own clusters.
- Cluster centers (the first search round) are stored on every node, so any node can answer round 1.

A search:

1. The client opens a stream on the router and sends the query. The router sends it unchanged to every node that holds the collection, and they expand it at the same time.
2. Each node answers with the center scores. The router passes the first answer on and checks that the others agree on the center count.
3. For each `SearchChosen`, the router drops repeated clusters, splits the list by owner, and sends each node only its own clusters. It passes the answers on unchanged, in request order, and sets `last` on the final one.
4. If a node does not answer, the router ends the stream with `ERROR_UNAVAILABLE`. The client retries on a new stream with the same decoys, never new ones.

Answers keep request order, so a replay returns the same bytes, also after a node restart. Expansion runs on every node that holds the collection, so work per query grows with the node count. Each node holds one search session per query.

## Run it

Start the nodes as usual (see [Configuration](../reference/configuration/server.md)), each with an `admin` API key for the router:

```bash
ombra_server --create-api-key --scope admin --data-dir /var/lib/ombra-a > node.key
```

Write the placement file (see [File formats](../reference/file-formats/placement-file.md)):

```text
ombra-placement 1
epoch 1
node a node-a.internal:50051
node b node-b.internal:50051
node c node-c.internal:50051
```

Create a key for the router's own clients, then start it:

```bash
ombra_router --create-api-key --scope admin --data-dir /var/lib/ombra-router
chmod 600 node.key
ombra_router --data-dir /var/lib/ombra-router --listen 0.0.0.0:50051 \
  --tls-cert cert.pem --tls-key key.pem \
  --placement /etc/ombra/placement --node-key-file node.key --node-tls-ca ca.pem
```

Clients connect with a key from the router's key file. The node key never leaves the router. `SIGHUP` re-reads the router's API key file and the placement file; an open stream keeps the map it started with.

## What the router does with each call

| Call | What the router does |
| --- | --- |
| `CreateCollection`, `DropCollection`, `GetCollection` | To every node of the collection. `GetCollection` adds the counts and answers `keys_uploaded` only when every node has the keys. A repeat of a cut-short `CreateCollection` finishes it when the nodes that have the collection hold the same spec. `DropCollection` goes to the first node first, because an alias may stop it. |
| `ListCollections` | Asks every node and merges by name. |
| `UploadKeys` | Streams the keys to every node at once; succeeds only when all accept. If a node is down it fails with `UNAVAILABLE`, and a repeat replaces the keys again on all nodes. The router keeps no keys. |
| `Insert` | Each block to the owner of its cluster. Center blocks go to every node. A repeat is safe: [inserts are idempotent](../reference/grpc/insert-and-delete.md#insert). |
| `Delete` | To every node. Ids a node does not hold are ignored. |
| `Search` | See above. |
| `ListBlocks` | Asks every node. Segment names get the node id in front (`a/seg-17`). Center blocks show once per node. |
| `GetBlock`, `ReplaceBlock` | Strip the node id and go to that node. A `ReplaceBlock` naming blocks of two nodes fails with `ERROR_BLOCK_MISMATCH`. |
| `RetrieveRecords` | Splits the scope by owner node, opens one sub-stream per node with the full row count, renumbers the parts, forwards encrypted queries and answers unchanged, and leaves out a node with no record in the scope. It never sees which rows were asked for. See [Private record retrieval](../how-it-works/private-retrieval.md). |
| `PutBlob`, `GetBlob`, `DeleteBlobs` | The node is chosen by the same hashing, from the blob id. |
| Alias calls | To the first node of the placement file, which holds the alias table. |

A call that cannot reach a node fails with gRPC `UNAVAILABLE` (a search stream ends with `ERROR_UNAVAILABLE`). The client retries both.

## Limits

- A node that is down makes every search that needs one of its clusters fail. There is one copy of each cluster; replicas are not built.
- The router has no `/metrics` and no REST port. Run the nodes with `--rest-port` to monitor them.
- Moving clusters between nodes is not built. Adding a node changes the owners of about 1 in n+1 clusters but the data does not move, so do not change the node list of a collection that holds data. Copy it into a new collection instead (see [Migrating a collection](migration.md)).
- A key limited to an alias does not work through the router. A key limited to a collection name does.
- The first node of the placement file holds the aliases, so a collection that uses an alias must be on that node.
- The router checks the API key in each call, but does not yet refuse a bad key before reading the message body.

## What each process learns

- The **router** sees what one server sees: every cluster number of every round, the ciphertexts in transit and the ids of blobs a client reads. For private retrieval it sees the clusters of the scope and the sizes of the encrypted queries, not the rows. It holds no segments and no HE key, but it holds an `admin` API key for every node, so a break-in at the router is as bad as one at a server.
- A **node** sees only its own part of each round, the ids and ciphertexts of its clusters, and its part of the blob store.
- **Placement never depends on the data.** If neighbouring clusters shared a node, that node could tell real clusters from decoys, because real ones would gather on it while decoys spread evenly. Hash placement prevents that.
- A router and all nodes colluding learn no more than one server. A replayed stream sends every node the same sub-requests, so a node learns nothing new.
