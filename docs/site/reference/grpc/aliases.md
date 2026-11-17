# Aliases

An alias is a name that points at one collection, so an application can keep one name while the collection behind it changes. The server keeps aliases in its catalog, so every client sees the same answer.

Alias names and collection names share one namespace: an alias cannot have the name of a collection, a collection cannot have the name of an alias, and an alias name cannot end in `.g<number>` (the names that re-clustering uses).

Only [`GetCollection`](collections.md#getcollection) resolves an alias: the answer carries the real name in `spec.name`. Every data method (`Insert`, `Search`, `GetBlob` and the rest) takes a real collection name and never resolves an alias. A client resolves the alias once when it opens the collection and keeps the real name. Searches that are running when the alias moves finish on the old collection, and a client sees the move when it opens the alias again.

`DropCollection` fails with `ERROR_ALIAS_CONFLICT` while an alias points at the collection. The collection that is only the `previous` target can be dropped, and the alias then forgets it. The three methods that change aliases need `admin` because a switch moves every client of a name, as a drop does. An `admin` key cannot be limited to a collection.

A key limited to a name that is an alias may use the alias's target and its previous target, and nothing else. The previous target keeps working for a drain period after a switch. The answer for any other name is the same whether that name exists or not. See [API keys](../../security/api-keys.md#keys-limited-to-an-alias).

## AliasInfo

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | The alias name. |
| `target` | string | The collection the alias points at. |
| `previous` | string | The target before the last switch. Empty if never switched. |
| `switched_at` | int64 | Time of the last switch, unix seconds. |

## CreateAlias

Makes a name an alias of one collection.

```proto
rpc CreateAlias(CreateAliasRequest) returns (CreateAliasResponse)
```

Scope: `admin`.

**CreateAliasRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | The alias name. |
| `target` | string | yes | The collection to point at. |

**CreateAliasResponse** (one of)

| Field | Type | Description |
| --- | --- | --- |
| `alias` | `AliasInfo` | The new alias. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_ALIAS_EXISTS` (the name is already a collection or an alias), `ERROR_COLLECTION_NOT_FOUND` (the target), `ERROR_INVALID_ARGUMENT`.

## SwitchAlias

Moves an alias to another collection if it points at the expected one now.

```proto
rpc SwitchAlias(SwitchAliasRequest) returns (SwitchAliasResponse)
```

Scope: `admin`.

**SwitchAliasRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | The alias name. |
| `new_target` | string | yes | The collection to move to. |
| `expected_target` | string | yes | The collection the alias must point at now. |

**SwitchAliasResponse** (one of)

| Field | Type | Description |
| --- | --- | --- |
| `alias` | `AliasInfo` | The alias after the switch. |
| `error` | `Error` | Set on failure. |

Errors: `ERROR_ALIAS_NOT_FOUND`, `ERROR_ALIAS_CONFLICT` (the alias does not point at `expected_target`), `ERROR_COLLECTION_NOT_FOUND` (the new target), `ERROR_INVALID_ARGUMENT`.

Notes:

- A compare-and-swap in one catalog transaction. It moves the alias to `new_target` only if the alias points at `expected_target` now. The old target becomes the alias's `previous`.
- A repeat of a switch that was applied succeeds and changes nothing (the alias is at `new_target` and its `previous` is `expected_target`), so a call whose answer was lost can be repeated. Switching to the current target with the current target as expected also succeeds.

## DropAlias

Removes an alias. The collection stays.

```proto
rpc DropAlias(DropAliasRequest) returns (DropAliasResponse)
```

Scope: `admin`.

**DropAliasRequest**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | The alias name. |

**DropAliasResponse**

| Field | Type | Description |
| --- | --- | --- |
| `error` | `Error` | Unset on success. |

Errors: `ERROR_ALIAS_NOT_FOUND`.

## ListAliases

Lists the aliases.

```proto
rpc ListAliases(ListAliasesRequest) returns (ListAliasesResponse)
```

Scope: `read`.

**ListAliasesRequest**: no fields.

**ListAliasesResponse**

| Field | Type | Description |
| --- | --- | --- |
| `aliases` | repeated `AliasInfo` | One entry per alias. |

Errors: none in the message.

Notes: a key limited to a collection sees only the alias it is limited to.
