# ombra-migrate

A client tool that moves a collection to a new embedding model, preset or key set with no downtime. Steps: [Migrating a collection](../../operations/migration.md). `python -m ombra.migrate` is the same.

```text
ombra-migrate STEP [--state FILE] [--address HOST:PORT | --local DIR] [options]
```

`STEP` is `plan`, `start`, `backfill`, `verify`, `switch`, `finish`, `abort` or `status`. The API key comes from `OMBRA_API_KEY`, the old key file's passphrase from `OMBRA_PASSPHRASE`, the new one's from `OMBRA_TARGET_PASSPHRASE`; otherwise it asks in a terminal. It never takes a passphrase as an argument.

- `--state FILE` (steps: all; default `ombra-migrate.json`): The state file. See [Migration state](../file-formats/migration-state.md).
- `--address HOST:PORT`, `--root-certificates PEM`, `--insecure` (steps: all; default none): The server, as for the Python client.
- `--local DIR` (steps: all; default none): A local-mode directory instead of a server. Local mode has no aliases.
- `--embedder pkg.mod:fn` (steps: `plan`, `start`, `backfill`; default none): Re-embed with this function. Without it the mode is rebuild.
- `--source NAME`, `--source-key-file F`, `--target-key-file F` (steps: `plan`; default none): The collection (or alias) to migrate, its key file, and the new key file to write. The new file must not exist.
- `--alias NAME` (steps: `plan`; default none, or the alias you named as the source): The alias that `switch` moves.
- `--target-preset NAME` (steps: `plan`; default the source's): The preset of the new collection.
- `--new-keys` (steps: `plan`; default off): Key rotation: same model and preset, new HE key pair and new master key. See [Rotating keys](../../operations/migration.md#rotating-keys). Refused with `--embedder`, `--model` or `--target-preset`.
- `--model LABEL` (steps: `plan`; default none): The model label for the [collection info](../file-formats/collection-info.md). Required with `--embedder`.
- `--filter-fields a,b` (steps: `plan`; default none): The names of the filter fields, in creation order.
- `--drain-hours H` (steps: `plan`; default 24): How long the old collection stays after `switch`.
- `--clusters N` (steps: `plan`; default none): The cluster count of the new collection.
- `--batch-size N` (steps: `backfill`; default 1024): Vectors per insert.
- `--sample N`, `-k K`, `--min-recall R` (steps: `verify`; default none): Queries to run, results per query, and the recall gate.
- `--force` (steps: `switch`, `finish`; default off): Skip the drain wait, or `switch` without a passed `verify`.

- 0: The step is done.
- 1: The step was refused or a check failed. The message says which.

## Example

```bash
ombra-migrate plan --address ombra.example.com:443 --source docs-v1 \
    --source-key-file docs-v1.key --target-key-file docs-v2.key \
    --alias docs --embedder mypkg.embed:encode --model "model-b"
ombra-migrate start|backfill|verify|switch --address ombra.example.com:443 [--embedder mypkg.embed:encode]
```
