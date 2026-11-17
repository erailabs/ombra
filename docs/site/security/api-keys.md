# API keys

An API key is an access token for the server. It is not an FHE key, and your secret key never leaves the client. The server stores only a salted hash of each key. You see the token once, when you create it.

## Scopes

A key has one scope. `read` may call the read methods and `search`. `write` may also change data. `admin` may call everything, including `DropCollection` and `/metrics`. The table of methods is on the [gRPC API](../reference/grpc/index.md) page.

## Keys on the HTTP port

The collection API on the HTTP port takes the same keys, sent as `Authorization: Bearer <key>`, and applies the same rules as the gRPC method that does the same work. Listing and reading a collection need `read`, creating one needs `write`, and dropping one needs `admin`. A key limited to one collection sees only that collection in the list, and any other name gets `403`, whether it exists or not. Since an `admin` key cannot be limited, a limited key can never drop a collection. Requests without a valid key get `401` and learn nothing. With `--no-auth` there is no check, and the server accepts that only on a loopback address. The table of routes is on the [REST API](../reference/rest/index.md) page.

## Limiting a key to one collection

A `read` or `write` key can be limited to one collection (it works the same over gRPC and over the [REST API](../reference/rest/index.md)):

```bash
ombra_server --create-api-key --scope write --collection docs --data-dir /data
```

A limited key can only call methods on that collection. Any call that names another collection fails with `PERMISSION_DENIED`, and the answer is the same whether that collection exists or not, so the key cannot be used to find out which names exist. `ListCollections` returns only the key's own collection. `CreateCollection` works only for that name. For calls that send a stream, the check happens on the first message, before the server stores any data or reads any key bytes.

An `admin` key cannot be limited: the server refuses to create one, and refuses a key file with such a line. An admin key can drop every collection and read the metrics, so a limit on it would mean nothing.

Limited keys do not make the server multi-tenant. Collections still share the GPU, the disk and the limits, and a client with a write key can fill the disk. For hard separation run one server per trust domain, see the [threat model](threat-model.md). `/metrics` on the HTTP port takes only an admin key.

## Keys limited to an alias

A key can be limited to the name of an [alias](../operations/migration.md#aliases) instead of a collection. Such a key may use the collection that the alias points at and the one it pointed at before the last switch, and nothing else. After a switch the key works on both collections during the drain period. After the next switch the oldest one is refused. The key file does not change: the `collection` field of a line is a name, and the server checks at each call whether that name is an alias.

The check needs one catalog read, and the answer is the same whether a name exists or not. A key limited to a name that is a collection is not widened: if someone later creates an alias with that name, the server refuses it, because alias names and collection names are one namespace. `ListCollections` returns the collections the key may use, and `ListAliases` returns only the alias it is limited to. Creating, switching and dropping aliases needs an `admin` key, which cannot be limited. So a switch is an operator action, and a limited key cannot move its own alias.

## The key file

The keys are in `api_keys.v1`, or `api_keys.v2` once a key is limited to a collection. See [File formats](../reference/file-formats/server-files.md#api-key-file). The file must be mode 0600. Create keys while the server runs and then send it SIGHUP to load them. To revoke a key, remove its line and send SIGHUP. A file that does not parse keeps the loaded keys and logs an error.

The first limited key moves the file to version 2 and keeps the old file as `api_keys.v1.migrated`. Old keys work as before. Editing `api_keys.v1.migrated` has no effect; delete it when you no longer need it. To go back to a server without version 2 support, keep a copy of the old file.
