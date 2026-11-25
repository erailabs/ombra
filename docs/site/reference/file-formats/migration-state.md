# Migration state

The state file of [`ombra-migrate`](../cli/ombra-migrate.md), default `ombra-migrate.json`. Version 1.

## Layout

A JSON object with these fields:

| Field | Meaning |
| --- | --- |
| `format` | `ombra-migrate` |
| `version` | 1 |
| other fields | The source and target names, the mode, the phase, the alias, the paths of the key files, the counts and the result of `verify`. |

It holds no passphrase, key, vector or document.

## Compatibility

The tool writes the file atomically and refuses a file with another format or version.
