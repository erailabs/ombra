# Durability

What the server guarantees when it stops without warning: a crash, `kill -9`, an out-of-memory kill or a removed container. It does not cover a disk that loses data after the server was told the write was safe, and it does not replace [backups](backups.md).

## What is guaranteed

- **A write the client got an answer for is kept**, exactly once: `insert`, `delete`, key upload, blob write or block replacement.
- **A write without an answer is all or nothing.** The client cannot tell which, so it checks. It can repeat a call only where the [Python API](../reference/python/errors-and-retries.md#retries) says so.
- **A block is never partial**, and an id is never in two blocks.
- **A delete request applies to all its ids or none.**
- **Keys are the old set or the new set.** An evaluation key upload replaces all three files or none.
- **A blob holds the old value or the new one**, never a mix.
- **Leftover files are removed at the next start.** Afterwards `ombra_verify` reports no problem and no orphan file. Recovery runs at start; there is no repair step.

A bulk insert is a stream of blocks, each following these rules alone. After a crash in the middle, some blocks can be stored and the rest not. The client saw an error for the whole call, so it checks which ids are stored before resending.

Re-clustering builds a new collection (`name.g1`) next to the old one. A crash during it leaves the old one unchanged and can leave a half-built new one. Drop the new one and start again.

## How this is tested

`tests/test_crash_recovery.py` runs the real server, writes with the real client, kills the process with `SIGKILL` mid-write, restarts it on the same data directory, runs `ombra_verify` and checks the points above. It covers insert, delete, compaction, block replacement (repack), re-clustering, key upload, blob write and delete, and dropping and creating a collection. The kill comes at a random moment (the seed is in the test name, so a failure can be rerun), or at one of the named crash points below, where a test build of the server stops itself. The release image has no crash points.

After the restart every case checks: nothing acknowledged is lost, no id is stored twice, a block is whole or absent, `ombra_verify` reports nothing, and the client can insert and delete again.

## Crash points

| Operation | Points (in order) | State after the stop |
| --- | --- | --- |
| Insert | `InsertBeforeSegmentRename`, `InsertAfterSegmentRename`, `InsertAfterUnlockedWrite` | The block file is written, the catalog does not have it. The file is removed at the restart. |
| Insert | `InsertBeforeIntent`, `InsertAfterIntent` | The log may hold an intent for a block not in the catalog. Recovery discards it and the file. |
| Insert | `InsertAfterCatalog`, `InsertAfterCommit` | The block is stored. A repeat of the same insert succeeds and stores nothing twice. |
| Delete | `DeleteBeforeRecord`, `DeleteAfterRecord`, `DeleteAfterCatalog` | All ids of the request are deleted or none. |
| Drop collection | `DropBeforeCatalog`, `DropAfterCatalog`, `DropAfterWalReset`, `DropAfterFirstFile` | The collection is there or gone. Leftover files are removed at the restart. |
| Block replacement | `ReplaceBeforeNewFile`, `ReplaceAfterNewFile`, `ReplaceAfterCatalog`, `ReplaceAfterFirstUnlink`, `ReplaceAfterUnlink` | Old blocks or new blocks, never both. The live ids do not change. |
| Compaction | `CompactBeforeCatalog`, `CompactAfterCatalog`, `CompactAfterUnlink` | Dead blocks are dropped or kept. Live ids do not change. |
| Blob write | `BlobBeforeRename`, `BlobAfterRename`, `BlobAfterCatalog` | The old value or the new one. |
| Blob delete | `BlobDeleteAfterCatalog` | The blob is gone, its file is removed at the restart. |

An insert takes the store lock in three steps: check, write the file without the lock, check again and commit. `InsertAfterSegmentRename` and `InsertAfterUnlockedWrite` lie in the unlocked write.

Also tested: **retry after a crash** (the same insert resent succeeds, each id is stored once, a second repeat changes nothing); **random kills** across all operations (`OMBRA_CRASH_ITERS`, default 20); and a **table check** that fails when a crash point is added to the code and not to this table. Run them with `make test-crash`; they need the Python client with OpenFHE, so `make test` skips them.

A `SIGKILL` loses nothing the server had already handed to the operating system. The tests do not cut the power; for that the server calls `fsync` before it answers. A full disk is covered by separate tests that make single writes fail. The answer to a failed disk is a [backup](backups.md).
