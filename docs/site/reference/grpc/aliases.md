# Aliases

An alias is a name that points at one collection, so an application keeps one name while the collection behind it changes. Aliases live in the server's catalog.

Alias and collection names share one namespace. An alias name cannot end in `.g<number>` (used by re-clustering).

Only [`GetCollection`](collections.md#getcollection) resolves an alias (the real name is in `spec.name`). Every data method takes a real collection name. A client resolves the alias once on open and keeps the real name; running searches finish on the old collection.

`DropCollection` fails with `ERROR_ALIAS_CONFLICT` while an alias points at the collection. A collection that is only a `previous` target can be dropped. The three methods that change aliases need `admin`, and an `admin` key cannot be limited to a collection.

A key limited to an alias may use the alias's target and its previous target (for a drain period after a switch), and nothing else. See [API keys](../../security/api-keys.md#keys-limited-to-an-alias).

## AliasInfo

- `name` (string): The alias name.
- `target` (string): The collection the alias points at.
- `previous` (string): The target before the last switch. Empty if never switched.
- `switched_at` (int64): Time of the last switch, unix seconds.

## CreateAlias

```proto
rpc CreateAlias(CreateAliasRequest) returns (CreateAliasResponse)
```

**CreateAliasRequest**

- `name` (string, required): The alias name.
- `target` (string, required): The collection to point at.

**CreateAliasResponse** (one of)

- `alias` (`AliasInfo`): The new alias.
- `error` (`Error`)

Errors: `ERROR_ALIAS_EXISTS` (the name is already a collection or an alias), `ERROR_COLLECTION_NOT_FOUND` (the target), `ERROR_INVALID_ARGUMENT`.

## SwitchAlias

```proto
rpc SwitchAlias(SwitchAliasRequest) returns (SwitchAliasResponse)
```

**SwitchAliasRequest**

- `name` (string, required): The alias name.
- `new_target` (string, required): The collection to move to.
- `expected_target` (string, required): The collection the alias must point at now.

**SwitchAliasResponse** (one of)

- `alias` (`AliasInfo`): The alias after the switch.
- `error` (`Error`)

Errors: `ERROR_ALIAS_NOT_FOUND`, `ERROR_ALIAS_CONFLICT` (the alias does not point at `expected_target`), `ERROR_COLLECTION_NOT_FOUND` (the new target), `ERROR_INVALID_ARGUMENT`.

Notes:

- Compare-and-swap in one catalog transaction. The old target becomes `previous`.
- Repeating an applied switch succeeds and changes nothing, so a call with a lost answer can be repeated. Switching to the current target with the current target as expected also succeeds.

## DropAlias

```proto
rpc DropAlias(DropAliasRequest) returns (DropAliasResponse)
```

**DropAliasRequest**

- `name` (string, required): The alias name.

**DropAliasResponse**

- `error` (`Error`)

Errors: `ERROR_ALIAS_NOT_FOUND`.

## ListAliases

```proto
rpc ListAliases(ListAliasesRequest) returns (ListAliasesResponse)
```

**ListAliasesRequest**: no fields.

**ListAliasesResponse**

- `aliases` (repeated `AliasInfo`): One entry per alias.

Errors: none in the message.

Notes: a key limited to a collection sees only the alias it is limited to.
