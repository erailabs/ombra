# Durability

This page says what the server guarantees when it stops without warning: a crash, `kill -9`, an out-of-memory kill or a container that is removed. It is about the process dying. It does not cover a disk that loses data after the server was told the write was safe, and it is not a replacement for [backups](backups.md).

## What is guaranteed

- **A write the client got an answer for is kept.** If `insert`, `delete`, a key upload, a blob write or a block replacement returned without an error, the data is on disk when the server starts again, exactly once.
- **A write without an answer is all or nothing.** If the server died before it answered, the write is either fully there or not there at all. The client cannot tell which, so it checks. It can repeat a call safely only where the [Python API](../reference/python-api.md) says so.
- **A block is never partial.** A block is stored and visible as a whole. An id is never in two blocks.
- **A delete request applies to all of its ids or to none.**
- **Keys are the old set or the new set.** An upload of evaluation keys replaces all three files together or none of them.
- **A blob holds the old value or the new one**, never a mix of both.
- **Files left over from the crash are removed at the next start.** After the restart `ombra_verify` reports no problem and no orphan file.
- **The server needs no repair step.** Recovery runs at start. You do not run a tool first.

A write that was in progress when the server died can be lost. That is the "without an answer" case above. A bulk insert is a stream of blocks, and each block follows the rule on its own. After a crash in the middle of a stream, some of its blocks can be stored and the rest not. The client saw an error for the whole call, so it checks which ids are stored before it sends them again.

Re-clustering builds a new collection (`name.g1`) next to the old one. A crash during it leaves the old collection unchanged and can leave a half-built new one. Drop the new one and start again.

## How this is tested

The test suite `tests/test_crash_recovery.py` runs the real server, writes to it with the real client, and kills the process with `SIGKILL` in the middle of the write. It restarts the server on the same data directory, runs `ombra_verify` and checks the points above. It does this for insert, delete, compaction, block replacement (repack), re-clustering, key upload, blob write and delete, and for dropping and creating a collection.

There are two ways to pick the moment of the kill:

- **Random time.** The kill comes at a random moment of the operation. The seed is part of the test name, so a failure can be run again.
- **Fixed points.** The storage code has named crash points, for example "the segment file is renamed, the catalog is not updated yet". A test build of the server stops itself at one of them. Every point is tried.

The fixed points exist only in test builds of the server. The release image is built without them.

## Crash points

Each fixed point stops the test server at one place. After the restart every case checks: nothing that was acknowledged is lost, no id is stored twice, a block is whole or absent, `ombra_verify` reports nothing (no orphan file after the restart), and the client can insert and delete again.

| Operation | Points (in order) | State after the stop |
| --- | --- | --- |
| Insert | `InsertBeforeSegmentRename`, `InsertAfterSegmentRename`, `InsertAfterUnlockedWrite` | The block file is written, the catalog does not have it. The file is removed at the restart. |
| Insert | `InsertBeforeIntent`, `InsertAfterIntent` | The log may hold an intent for a block that is not in the catalog. Recovery discards it and the file. |
| Insert | `InsertAfterCatalog`, `InsertAfterCommit` | The block is stored. A repeat of the same insert succeeds and stores nothing twice. |
| Delete | `DeleteBeforeRecord`, `DeleteAfterRecord`, `DeleteAfterCatalog` | All ids of the request are deleted or none. |
| Drop collection | `DropBeforeCatalog`, `DropAfterCatalog`, `DropAfterWalReset`, `DropAfterFirstFile` | The collection is there or gone. Files that are left are removed at the restart. |
| Block replacement | `ReplaceBeforeNewFile`, `ReplaceAfterNewFile`, `ReplaceAfterCatalog`, `ReplaceAfterFirstUnlink`, `ReplaceAfterUnlink` | Old blocks or new blocks, never both. The live ids do not change. |
| Compaction | `CompactBeforeCatalog`, `CompactAfterCatalog`, `CompactAfterUnlink` | Dead blocks are dropped or kept. Live ids do not change. |
| Blob write | `BlobBeforeRename`, `BlobAfterRename`, `BlobAfterCatalog` | The old value or the new one. |
| Blob delete | `BlobDeleteAfterCatalog` | The blob is gone, its file is removed at the restart. |

An insert takes the store lock in three steps: check, then write the file without the lock, then check again and commit. The points `InsertAfterSegmentRename` and `InsertAfterUnlockedWrite` lie in the unlocked write, before the second check.

Three more tests run on top of the points:

- **Retry after a crash.** After a stop in the middle of an insert, the same insert is sent again. It succeeds, each id is stored once, and a second repeat changes nothing.
- **Random kills.** Insert, insert stream, delete, compaction, block replacement, re-clustering, key upload, blob write and delete, drop and create. Set `OMBRA_CRASH_ITERS` to change how many (default 20).
- **Table check.** A test fails when a crash point is added to the code and not to this table.

Run them with `make test-crash`. They are long and need the Python client with OpenFHE, so `make test` does not run them.

A `SIGKILL` loses nothing that the server had already handed to the operating system. The tests do not cut the power. For that case the server calls `fsync` before it answers. A full disk is covered by separate tests that make single writes fail. The answer to a failed disk is a [backup](backups.md).
