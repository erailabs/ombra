# Migrating a collection

Some changes cannot be made in place. A new embedding model changes the meaning of every vector, a new dimension changes the layout, and a new preset or OpenFHE version changes the ciphertexts. The server cannot transform encrypted data, so these changes build a new collection beside the old one, move your application to it, and drop the old one later. `ombra-migrate` does this with no downtime. It is also the way to re-key a collection: the new collection always gets a new FHE key pair and a new document master key.

The pieces are:

- **Aliases.** A name on the server that points at one collection. The application opens the alias, and a switch moves it in one step. See [Aliases](#aliases).
- **`ombra-migrate`.** A command line tool and a Python API (`ombra.migrate`) that build the new collection, check it, switch the alias and drop the old collection.
- **Collection info.** A small encrypted record on the new collection with the name of the embedding model, so the application can check that its embedder matches.

## Aliases

An alias is a name that resolves to a collection when a client opens it. Create one once, and let the application use the alias instead of the collection name.

```python
client.create_alias("docs", "docs-v1")      # needs an admin key
client.open_collection("docs", key_file="docs-v1.key", passphrase=pw)   # returns "docs-v1"
```

`open_collection` asks the server what the alias points at and keeps the real name. After that the collection object works on that collection, also after the alias moves. It never follows a switch by itself, because its keys belong to one collection: a query made for one key set fails on the other. To follow a switch, open the alias again (for example every few minutes, or on a signal). Searches that are running when the switch happens finish on the old collection.

Rules:

- Alias names and collection names share one namespace. An alias cannot have the name of a collection, and a collection cannot have the name of an alias.
- An alias name cannot end in `.g` and a number, because re-clustering uses those names.
- Only `GetCollection` resolves an alias. Inserts, searches, blob calls and `DropCollection` take real names.
- `SwitchAlias` is a compare-and-swap: it moves the alias only if it points at the collection you expect. If another process moved it first, the call fails with `AliasConflictError` and changes nothing. A repeat of a switch that already happened succeeds.
- A collection that an alias points at cannot be dropped. The collection that the alias pointed at before the last switch can be.
- Creating, switching and dropping an alias needs an `admin` key. Listing needs `read`.
- Local mode has no aliases. The alias calls fail with a clear error there, and `ombra-migrate` records the switch and tells you the new name.

API keys limited to a collection follow aliases. A key limited to the name of an alias may use the alias's target and its previous target. After a switch the old collection stays usable for the key during the drain period, and stops being usable at the next switch. See [API keys](../security/api-keys.md#keys-limited-to-an-alias).

## The steps

```text
plan  ->  start  ->  backfill  ->  verify  ->  switch  ->  finish
                                       \--> abort (any time before switch)
```

Every step reads and writes a state file, `ombra-migrate.json`, so you can stop after any step and continue later, even from another machine. The file holds names, counts and the phase, and no passphrase, key or data. The tool needs the key file of the old collection, the passphrase for it, and a path for the new key file. Give the passphrases in `OMBRA_PASSPHRASE` (old key file) and `OMBRA_TARGET_PASSPHRASE` (new key file), or type them when asked. The API key comes from `OMBRA_API_KEY` and must be an admin key without a collection limit, since the tool creates a collection, switches an alias and drops one.

| Step | What it does |
| --- | --- |
| `plan` | Picks the new name, `<base>.m<n>` (for example `docs.m1`), and writes the state file. Creates nothing on the server. |
| `start` | Makes the new key file (written before the server gets a byte), clusters a sample of the vectors, creates the collection, and stores the collection info. |
| `backfill` | Copies every live vector the new collection does not have yet, in batches. Safe to run again. |
| `verify` | Runs the checks below. |
| `switch` | Moves the alias to the new collection with one compare-and-swap. Needs a passed `verify`. |
| `finish` | Drops the old collection after the drain period. The old key file is kept. |
| `abort` | Drops the new collection and stops. Not possible after `switch`. |

### Two modes

**Rebuild.** The model stays the same. Use it for a new preset, a new OpenFHE version, new keys or denser blocks. The vectors come from the exact float32 copies in the encrypted record blobs of the old collection.

**Re-embed.** A new model. Give `--embedder pkg.mod:function` and `--model LABEL`. The function takes a list of texts and returns one vector per text, as an array of shape `(n, d)`. The texts are the documents of the old collection, so every vector needs a text document. The dimension of the new model must be one the presets support. The tool normalizes the vectors.

### Example

```bash
export OMBRA_API_KEY=...            # an admin key
export OMBRA_PASSPHRASE=...         # passphrase of docs-v1.key
export OMBRA_TARGET_PASSPHRASE=...  # passphrase for the new key file

ombra-migrate plan --address ombra.example.com:443 --source docs-v1 \
    --source-key-file docs-v1.key --target-key-file docs-v2.key \
    --alias docs --filter-fields lang --embedder mypkg.embed:encode --model "model-b"
ombra-migrate start     --address ombra.example.com:443 --embedder mypkg.embed:encode
ombra-migrate backfill  --address ombra.example.com:443 --embedder mypkg.embed:encode
ombra-migrate verify    --address ombra.example.com:443
ombra-migrate switch    --address ombra.example.com:443
# ... after the drain period (24 hours by default) ...
ombra-migrate finish    --address ombra.example.com:443
```

`--filter-fields` names the filter fields of the old collection in their creation order, because the server stores only their sizes. Use `--local DIR` instead of `--address` for local mode. `--insecure` and `--root-certificates` work as in the Python client. The options are listed on the [command line tools](../reference/cli.md#ombra-migrate) page.

### Writes during a migration

Between `start` and `switch` your application keeps writing to the old collection. Send its inserts and deletes through the migration object, which writes to both collections (the Python API):

```python
from ombra.migrate import Migration

m = Migration.resume(client, state_path="ombra-migrate.json",
                     source_passphrase=pw_old, target_passphrase=pw_new, embed=encode)
m.insert(old_vectors, ids, metadata, documents)   # to both; re-embedded for the new one
m.delete(ids)                                     # from both
```

In re-embed mode `insert` embeds the documents with your function, or takes `new_vectors=` if you have them. An insert that bypasses the migration object after the last `backfill` is missed. Run `backfill` again before `verify`: it copies what the new collection lacks. A write to the migration object after `verify` makes the migration unverified, so run `verify` again.

### The checks

`verify` fails, and `switch` refuses, unless all of these hold:

| Check | Meaning |
| --- | --- |
| `counts_equal` | The old and the new collection have the same number of live vectors. |
| `all_have_records` | Every vector in the new collection has its encrypted record blob. |
| `self_retrieval` | For a sample of vectors, searching the new collection with the vector finds that vector among the top 5. At least 95% must. |
| `recall` | For the same sample, the encrypted search returns at least 90% of the top `k` that exact search over the stored vectors returns (`--min-recall`). |

The result also reports the overlap with the old collection: how many of the new top `k` the old collection returned for the same vector. This is not a check. A new model has different neighbours, so a low overlap is expected in re-embed mode. In rebuild mode it should be close to 1.

`verify` needs a server that can search, so it needs a GPU server or local mode. It reads every record of both collections, so it takes time on a large collection. The sample is 20 vectors by default (`--sample`).

### The drain period

After `switch`, the old collection stays for the drain period, 24 hours by default (`--drain-hours` at `plan`). Clients that opened the alias before the switch keep working on it. `finish` refuses before the period is over unless you pass `--force`. It also refuses if the alias no longer points at the new collection. Keep the old key file for as long as you keep backups of the old collection.

## The collection info

The new collection has an encrypted record under a reserved id with the model label, the name of the source, the migration number, the preset and the creation time. Read it in your application:

```python
info = client.get_collection_info("docs-v2")     # {"model": "model-b", ...}
assert info["model"] == MY_EMBEDDER_LABEL
```

The server sees an encrypted blob like any other. In rebuild mode without `--model`, the label of the old collection carries over. Its format is on [File formats](../reference/file-formats.md#collection-info).

## What it costs and what it does not do

- Storage doubles until `finish`.
- The tool reads every block of the old collection to learn its ids, and every record and document blob, and it uploads all of them again. The time is dominated by encryption on your machine. Plan for the same work as the first load.
- Blobs are opened with the old keys and sealed again with the new ones, so the document master key changes too. The old key file does not open the new collection.
- A document stored by reference (above the inline limit) cannot be copied. The tool stops with an error naming the problem.
- It does not change the preset's security parameters or the number of filter fields.
- Collections that re-clustering made (`<base>.g<n>`) are separate collections: migrate the one you use.
