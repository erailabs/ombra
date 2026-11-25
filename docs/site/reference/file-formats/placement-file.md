# Placement file

Read by `ombra_router` (see [Several nodes and a router](../../operations/routing.md) and the [router flags](../configuration/router.md)). Version 1. UTF-8 text, one record per line; lines starting with `#` are comments.

```text
ombra-placement 1
epoch 7
node a 10.0.0.1:50051
node b 10.0.0.2:50051
collection docs a b
```

## Layout

- `ombra-placement 1`: The version header. Must be the first line.
- `epoch N`: A counter of map changes, written to the log when the map is loaded.
- `node ID HOST:PORT`: A node. `ID` is 1 to 64 characters from `A-Z a-z 0-9 . _ -`. The order of the `node` lines is the order of the nodes. The first node holds the alias table.
- `collection NAME ID...`: Limits a collection to the listed nodes. A collection with no line is on every node.

## Compatibility

The router refuses another version or first line, a duplicate node id, a collection that names an unknown node, and a file with no node.

The file holds no cluster numbers: the owner of a cluster is computed from the node ids (highest random weight of cluster number and node id). The router never writes the file; tools that do use a temporary file, a sync and a rename. The router re-reads it on `SIGHUP`.
