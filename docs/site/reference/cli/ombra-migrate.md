# ombra-migrate

A client tool, not a server tool: it holds your key files, so it runs where your application runs. It moves a collection to a new embedding model, preset or key set with no downtime. The steps and their meaning are on [Migrating a collection](../../operations/migration.md). `python -m ombra.migrate` is the same.

```text
ombra-migrate STEP [--state FILE] [--address HOST:PORT | --local DIR] [options]
```

`STEP` is `plan`, `start`, `backfill`, `verify`, `switch`, `finish`, `abort` or `status`. The API key comes from `OMBRA_API_KEY`, the passphrase of the old key file from `OMBRA_PASSPHRASE` and that of the new key file from `OMBRA_TARGET_PASSPHRASE`. Without those variables it asks in a terminal. The tool never takes a passphrase as an argument.

| Option | Steps | Default | Description |
| --- | --- | --- | --- |
| `--state FILE` | all | `ombra-migrate.json` | The state file. See [Migration state](../file-formats/migration-state.md). |
| `--address HOST:PORT`, `--root-certificates PEM`, `--insecure` | all | none | The server, as for the Python client. |
| `--local DIR` | all | none | A local-mode directory instead of a server. Local mode has no aliases. |
| `--embedder pkg.mod:fn` | `plan`, `start`, `backfill` | none | Re-embed with this function. Without it the mode is rebuild. |
| `--source NAME`, `--source-key-file F`, `--target-key-file F` | `plan` | none | The collection (or alias) to migrate, its key file, and the new key file to write. The new file must not exist. |
| `--alias NAME` | `plan` | none, or the alias you named as the source | The alias that `switch` moves. |
| `--target-preset NAME` | `plan` | the source's | The preset of the new collection. |
| `--new-keys` | `plan` | off | Key rotation: same model and preset, new HE key pair and new master key. See [Rotating keys](../../operations/migration.md#rotating-keys). Refused with `--embedder`, `--model` or `--target-preset`. |
| `--model LABEL` | `plan` | none | The model label for the [collection info](../file-formats/collection-info.md). Required with `--embedder`. |
| `--filter-fields a,b` | `plan` | none | The names of the filter fields, in creation order. |
| `--drain-hours H` | `plan` | 24 | How long the old collection stays after `switch`. |
| `--clusters N` | `plan` | none | The cluster count of the new collection. |
| `--batch-size N` | `backfill` | 1024 | Vectors per insert. |
| `--sample N`, `-k K`, `--min-recall R` | `verify` | none | Queries to run, results per query, and the recall gate. |
| `--force` | `switch`, `finish` | off | Skip the drain wait, or `switch` without a passed `verify`. |

| Exit code | Meaning |
| --- | --- |
| 0 | The step is done. |
| 1 | The step was refused or a check failed. The message says which. |

## Example

```bash
ombra-migrate plan --address ombra.example.com:443 --source docs-v1 \
    --source-key-file docs-v1.key --target-key-file docs-v2.key \
    --alias docs --filter-fields lang --embedder mypkg.embed:encode --model "model-b"
ombra-migrate start    --address ombra.example.com:443 --embedder mypkg.embed:encode
ombra-migrate backfill --address ombra.example.com:443 --embedder mypkg.embed:encode
ombra-migrate verify   --address ombra.example.com:443
ombra-migrate switch   --address ombra.example.com:443
```
