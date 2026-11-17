# Command line tools

| Tool | Runs on | What it does |
| --- | --- | --- |
| [`ombra_verify`](ombra-verify.md) | server | Checks a data directory for corruption. |
| [`ombra_backup`](ombra-backup.md) | server | Copies a data directory while the server runs, and verifies the copy. |
| [`ombra-migrate`](ombra-migrate.md) | client | Moves a collection to a new model, preset or key set with no downtime. |
| [`ombra-keys`](ombra-keys.md) | client | Works with key files. |

`ombra_verify` and `ombra_backup` run on the CPU, need no GPU and no OpenFHE, and never read a secret key (the server has none). They ship in the server image next to `ombra_server`, in `/usr/local/bin`. The server itself is described on the [Configuration](../configuration/server.md) page.

Both print counts and file names only. They never print vector ids, collection names or any content. File names are counters such as `s0000000000000007.oseg`.

`ombra-migrate` and `ombra-keys` hold your key files, so they run where your application runs.
