# Command line tools

| Tool | Runs on | What it does |
| --- | --- | --- |
| [`ombra_verify`](ombra-verify.md) | server | Checks a data directory for corruption. |
| [`ombra_backup`](ombra-backup.md) | server | Copies a data directory while the server runs, and verifies the copy. |
| [`ombra-migrate`](ombra-migrate.md) | client | Moves a collection to a new model, preset or key set with no downtime. |
| [`ombra-keys`](ombra-keys.md) | client | Works with key files. |

`ombra_verify` and `ombra_backup` run on the CPU, need no GPU and no OpenFHE, and ship in the server image in `/usr/local/bin`. They print counts and file names only (such as `s0000000000000007.oseg`), never ids, collection names or content. See also [Configuration](../configuration/server.md).

`ombra-migrate` and `ombra-keys` hold your key files, so they run where your application runs.
