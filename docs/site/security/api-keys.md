# API keys

An API key is an access token for the server. It is not an HE key, and your secret key never leaves the client. The server stores only a salted hash of each key. You see the token once, when you create it.

## Scopes

A key has one scope. `read` may call the read methods and `search`. `write` may also change data. `admin` may call everything, including `DropCollection` and `/metrics`. The methods are in the [gRPC API](../reference/grpc/index.md) table.

The HTTP port takes the same keys as `Authorization: Bearer <key>`, with the rules of the matching gRPC method: listing and reading a collection need `read`, creating needs `write`, dropping needs `admin`. Requests without a valid key get `401` and learn nothing. With `--no-auth` there is no check, accepted only on a loopback address. See the [REST API](../reference/rest/index.md).

## Limiting a key to one collection

A `read` or `write` key can be limited to one collection, over gRPC and REST alike:

```bash
ombra_server --create-api-key --scope write --collection docs --data-dir /data
```

A call naming another collection fails with `PERMISSION_DENIED` (REST: `403`), the same whether that collection exists or not. `ListCollections` returns only the key's own collection, and `CreateCollection` works only for that name. For streams, the check happens on the first message, before the server stores data or reads key bytes.

An `admin` key cannot be limited: the server refuses to create one, and refuses a key file with such a line. So a limited key can never drop a collection or read `/metrics`.

Limited keys do not make the server multi-tenant: collections still share the GPU, disk and limits, and a write key can fill the disk. For hard separation run one server per trust domain, see the [threat model](threat-model.md).

## Keys limited to an alias

A key can be limited to the name of an [alias](../operations/migration.md#aliases). It may use the collection the alias points at and the one it pointed at before the last switch, and nothing else; after the next switch the older one is refused. The key file does not change: the server checks at each call whether the `collection` name is an alias.

The answer is the same whether a name exists or not. A key limited to a collection name is not widened if someone later creates an alias with that name: the server refuses it, because the namespace is shared. `ListCollections` returns the collections the key may use, and `ListAliases` only its own alias. Alias changes need `admin`, so a limited key cannot move its own alias.

## The key file

Keys are in `api_keys.v1`, or `api_keys.v2` once a key is limited to a collection (see [File formats](../reference/file-formats/server-files.md#api-key-file)). The file must be mode 0600. Create keys while the server runs, then send it SIGHUP to load them. To revoke a key, remove its line and send SIGHUP. A file that does not parse keeps the loaded keys and logs an error.

The first limited key moves the file to version 2 and keeps the old one as `api_keys.v1.migrated`. Old keys work as before. Editing the migrated file has no effect; delete it when you no longer need it. To go back to a server without version 2 support, keep a copy of the old file.
