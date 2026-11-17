# Placement file

The map that `ombra_router` reads (see [Several nodes and a router](../../operations/routing.md) and the [router flags](../configuration/router.md)). Version 1. UTF-8 text with one record per line. A line that starts with `#` is a comment.

```text
ombra-placement 1
epoch 7
node a 10.0.0.1:50051
node b 10.0.0.2:50051
collection docs a b
```

## Layout

| Line | Meaning |
| --- | --- |
| `ombra-placement 1` | The version header. Must be the first line. |
| `epoch N` | A counter of map changes, written to the log when the map is loaded. |
| `node ID HOST:PORT` | A node. `ID` is 1 to 64 characters from `A-Z a-z 0-9 . _ -`. The order of the `node` lines is the order of the nodes. The first node holds the alias table. |
| `collection NAME ID...` | Limits a collection to the listed nodes. A collection with no line is on every node. |

## Compatibility

The router refuses any other version, and any other first line. A duplicate node id, a collection that names an unknown node and a file with no node are refused.

The file holds no cluster numbers: the owner of a cluster is computed from the node ids (highest random weight of cluster number and node id), so it never depends on the data.

The router never writes the file. Tools that do write it with a temporary file, a sync and a rename. The router re-reads it on `SIGHUP`.
