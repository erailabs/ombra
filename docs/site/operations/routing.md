# Several nodes and a router

One `ombra_server` serves one data directory. To hold more clusters than one machine can, run several nodes and put `ombra_router` in front of them. Clients talk to the router with the same API, so no client code changes, and the nodes are plain `ombra_server` processes.

The router never decrypts anything and holds no HE key. It reads the placement file, splits each call by cluster, and passes the encrypted answers on as bytes.

## How it works

Each cluster of a collection lives on exactly one node. The owner is the node with the highest random weight for the pair (cluster number, node id). This is rendezvous hashing, with three properties:

- The owner depends on the cluster number and the node ids only. It never depends on the vectors, the cluster centers or the number of vectors in a cluster. See [what each process learns](#what-each-process-learns).
- Adding a node to n others moves about 1 in n+1 clusters, all of them to the new node. Removing a node moves only its own clusters.
- The cluster centers (the first search round) are stored on every node, so any node can answer round 1.

A search runs like this:

1. The client opens a stream on the router and sends the query. The router sends it unchanged to every node that holds the collection. The nodes expand the query at the same time.
2. Each node answers with the center scores. The router passes the first node's answer on and checks that the others agree on the center count.
3. For each `SearchChosen`, the router drops repeated clusters, splits the list by owner, and sends each node only its own clusters. It passes the node answers to the client unchanged, in the order of the request, and sets `last` on the final one.
4. If a node does not answer, the router ends the stream with `ERROR_UNAVAILABLE`. The client treats it as retryable and sends the same requests again on a new stream, with the same decoys. It never draws new decoys.

The answers keep the order of the request, so a replay returns the same bytes, also after a node restart.

Expansion runs once on every node that holds the collection, so the work per query grows with the number of nodes. Each node also holds one search session per query.

## Run it

Start the nodes as usual (see [Configuration](../reference/configuration/server.md)). Give each node an API key with the `admin` scope for the router to use:

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

Create a key file for the router's own clients, then start it:

```bash
ombra_router --create-api-key --scope admin --data-dir /var/lib/ombra-router
chmod 600 node.key
ombra_router --data-dir /var/lib/ombra-router --listen 0.0.0.0:50051 \
  --tls-cert cert.pem --tls-key key.pem \
  --placement /etc/ombra/placement --node-key-file node.key --node-tls-ca ca.pem
```

Clients connect to the router with a key from the router's key file. The node key never leaves the router. Send `SIGHUP` to the router to re-read its API key file and the placement file. A stream that is open keeps the map it started with.

## What the router does with each call

| Call | What the router does |
| --- | --- |
| `CreateCollection`, `DropCollection`, `GetCollection` | Goes to every node of the collection. `GetCollection` adds the counts and answers `keys_uploaded` only when every node has the keys. A repeat of a `CreateCollection` that was cut short finishes it when the nodes that have the collection hold the same spec. `DropCollection` goes to the first node first, because an alias may stop it. |
| `ListCollections` | Asks every node and merges by name. |
| `UploadKeys` | Streams the keys to every node at once. It succeeds only when every node accepted them. If a node is down the call fails with `UNAVAILABLE`, and a repeat replaces the keys again on all nodes. The router keeps no keys. |
| `Insert` | Sends each block to the owner of its cluster. Center blocks go to every node. A repeat of an insert is safe because [inserts are idempotent](../reference/grpc/insert-and-delete.md#insert). |
| `Delete` | Goes to every node. Ids that a node does not hold are ignored. |
| `Search` | See above. |
| `ListBlocks` | Asks every node. Segment names get the node id in front (`a/seg-17`). Center blocks show up once per node. |
| `GetBlock`, `ReplaceBlock` | Strip the node id and go to that node. A `ReplaceBlock` that names blocks of two nodes fails with `ERROR_BLOCK_MISMATCH`. |
| `PutBlob`, `GetBlob`, `DeleteBlobs` | The node is chosen by the same hashing, from the blob id. |
| Alias calls | Go to the first node of the placement file, which holds the alias table. The other nodes keep no aliases. |

A call that cannot reach a node fails with the gRPC status `UNAVAILABLE` (a search stream ends with `ERROR_UNAVAILABLE` instead). The client retries both.

## Limits

- A node that goes down makes every search that needs one of its clusters fail until it is back. There is one copy of each cluster. Replicas are not built yet.
- The router has no `/metrics` and no REST port. Run `ombra_server` nodes with `--rest-port` to monitor them.
- Moving a cluster from one node to another is not built. Adding a node to the file changes the owners of about 1 in n+1 clusters, and the data does not move with them, so do not change the node list of a collection that holds data. Copy the data into a new collection instead (see [Migrating a collection](migration.md)).
- A key limited to an alias does not work through the router. A key limited to a collection name does.
- The first node of the placement file holds the aliases, so a collection that uses an alias must be on that node.
- The router checks the API key in each call, but it does not yet refuse a bad key before it reads the message body, as `ombra_server` does with TLS.

## What each process learns

- The **router** sees what one server sees today: every cluster number of every round, the ciphertexts in transit and the ids of the blobs a client reads. It does not hold the stored segments, and it holds no HE key. It does hold an `admin` API key for every node, so a break-in at the router is as bad as a break-in at a server.
- A **node** sees only the part of each round it owns, the ids and ciphertexts of its own clusters, and its own part of the blob store.
- **Placement never depends on the data.** The owner of a cluster is a function of the cluster number and the node ids. If neighbouring clusters were put on one node, that node could tell real clusters from decoys, because real clusters would gather on it while decoys spread evenly. Putting them by hash prevents that.
- A router and all nodes that collude learn no more than one server learns today.
- When a stream is replayed after a failure, every node gets the same sub-requests as before, so a node learns nothing new.
