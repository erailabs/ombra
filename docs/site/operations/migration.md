# Migrating a collection

Some changes cannot be made in place: a new embedding model, a new dimension, a new preset or OpenFHE version. The server cannot transform encrypted data, so `ombra-migrate` builds a new collection beside the old one, switches your application to it with no downtime, and drops the old one later. The new collection always gets a new HE key pair and document master key, so the tool also re-keys.

## Aliases

An alias is a name that resolves to a collection when a client opens it. The application uses the alias; a switch moves it in one step.

```python
client.create_alias("docs", "docs-v1")  # needs an admin key
client.open_collection("docs", key_file="docs-v1.key", passphrase=pw)  # returns "docs-v1"
```

The collection object keeps the real name after the alias moves, because its keys belong to one collection. To follow a switch, open the alias again (every few minutes, or on a signal). Running searches finish on the old collection.

- Alias and collection names share one namespace. An alias name cannot end in `.g` and a number.
- Only `GetCollection` resolves an alias. Inserts, searches, blob calls and `DropCollection` take real names.
- `SwitchAlias` is a compare-and-swap: it moves the alias only if it points at the collection you expect, otherwise it fails with `AliasConflictError`. A repeat of a completed switch succeeds.
- A collection an alias points at cannot be dropped. The previous target can be.
- Creating, switching and dropping need `admin`; listing needs `read`.
- Local mode has no aliases: the calls fail with a clear error, and `ombra-migrate` tells you the new name.

Limited API keys follow aliases, see [API keys](../security/api-keys.md#keys-limited-to-an-alias).

## The steps

```text
plan  ->  start  ->  backfill  ->  verify  ->  switch  ->  finish
                                       \--> abort (any time before switch)
```

Each step reads and writes a state file, `ombra-migrate.json` (names, counts, phase; no passphrase, key or data), so you can continue later from another machine. The tool needs the old key file, its passphrase (`OMBRA_PASSPHRASE`), a path for the new key file and its passphrase (`OMBRA_TARGET_PASSPHRASE`), or prompts. `OMBRA_API_KEY` must be an admin key without a collection limit.

| Step | What it does |
| --- | --- |
| `plan` | Picks the new name `<base>.m<n>` (for example `docs.m1`) and writes the state file. Creates nothing on the server. |
| `start` | Makes the new key file (before the server gets a byte), clusters a sample, creates the collection and stores the collection info. |
| `backfill` | Copies every live vector the new collection lacks, in batches. Safe to repeat. |
| `verify` | Runs the checks below. |
| `switch` | Moves the alias. Needs a passed `verify`. |
| `finish` | Drops the old collection after the drain period. The old key file is kept. |
| `abort` | Drops the new collection. Not possible after `switch`. |

**Rebuild** keeps the model (new preset, OpenFHE version, keys or denser blocks). Vectors come from the float32 copies in the old collection's record blobs. **Re-embed** is for a new model: give `--embedder pkg.mod:function` (a list of texts in, an `(n, d)` array out) and `--model LABEL`. The texts are the old collection's documents, so every vector needs a text document. The new dimension must be one the presets support. The tool normalizes the vectors.

```bash
export OMBRA_API_KEY=... OMBRA_PASSPHRASE=... OMBRA_TARGET_PASSPHRASE=...

ombra-migrate plan --address ombra.example.com:443 --source docs-v1 \
    --source-key-file docs-v1.key --target-key-file docs-v2.key \
    --alias docs --filter-fields lang --embedder mypkg.embed:encode --model "model-b"
ombra-migrate start     --address ombra.example.com:443 --embedder mypkg.embed:encode
ombra-migrate backfill  --address ombra.example.com:443 --embedder mypkg.embed:encode
ombra-migrate verify    --address ombra.example.com:443
ombra-migrate switch    --address ombra.example.com:443
ombra-migrate finish    --address ombra.example.com:443   # after the drain period
```

`--filter-fields` names the old collection's filter fields in creation order, because the server stores only their sizes. Use `--local DIR` instead of `--address` for local mode. All options: [command line tools](../reference/cli/ombra-migrate.md).

### Rotating keys

`ombra-migrate plan --new-keys` replaces the keys and nothing else. It makes a new OpenFHE key pair and document master key, writes them to the new key file (synced to disk) before the server receives anything, encrypts every vector again from its float32 copy, and seals every record, document, stats and info blob again. It never converts old ciphertexts, so the old key does not touch the new data. It is refused together with `--embedder`, `--model` and `--target-preset`. The transfer is about 16 GB down and 41 GB up per million vectors at dimension 384, plus documents.

After `finish`, the old key file still opens any copy made before (a server backup, disk image or copy of the blobs). Delete those copies when you no longer need them, then destroy the old key file and its backups. Until then, keep it secret.

### Writes during a migration

Between `start` and `switch` the application keeps writing to the old collection. Send inserts and deletes through the migration object, which writes to both (Python API), or pause writes and run `backfill` once more.

```python
m = Migration.resume(client, state_path="ombra-migrate.json",
                     source_passphrase=pw_old, target_passphrase=pw_new, embed=encode)
m.insert(old_vectors, ids, metadata, documents)  # to both; re-embedded for the new one
m.delete(ids)  # from both
```

In re-embed mode `insert` embeds the documents with your function, or takes `new_vectors=`. An insert that bypasses the migration object after the last `backfill` is missed, so run `backfill` again before `verify`. A write after `verify` makes the migration unverified: run `verify` again.

### The checks

`verify` fails, and `switch` refuses, unless all hold:

| Check | Meaning |
| --- | --- |
| `counts_equal` | Old and new collection have the same number of live vectors. |
| `all_have_records` | Every vector in the new collection has its encrypted record blob. |
| `self_retrieval` | For a sample of vectors, searching the new collection finds the vector in the top 5. At least 95% must. |
| `recall` | For the same sample, the encrypted search returns at least 90% of the top `k` of exact search over the stored vectors (`--min-recall`). |

The result also reports the overlap with the old collection (not a check; low is expected in re-embed mode, near 1 in rebuild). `verify` needs a server that can search and reads every record of both collections. The sample is 20 vectors (`--sample`).

### The drain period

After `switch` the old collection stays for 24 hours by default (`--drain-hours` at `plan`), so clients that opened the alias earlier keep working. `finish` refuses before it ends unless you pass `--force`, and refuses if the alias no longer points at the new collection. Keep the old key file as long as you keep backups of the old collection.

## The collection info

The new collection has an encrypted record under a reserved id with the model label, source name, migration number, preset and creation time (see [File formats](../reference/file-formats/collection-info.md)). The server sees an encrypted blob. In rebuild mode without `--model` the old label carries over. Read it with `client.get_collection_info("docs-v2")`, for example to check `info["model"]` against your embedder.

## Costs and limits

- Storage doubles until `finish`. The tool reads and re-uploads every block, record blob and document blob; plan for the same work as the first load.
- The target key file must differ from the source's.
- A document stored by reference (above the inline limit) cannot be copied; the tool stops with an error.
- It does not change the preset's security parameters or the number of filter fields.
- Collections made by re-clustering (`<base>.g<n>`) are separate: migrate the one you use.
