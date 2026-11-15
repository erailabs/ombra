// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Crash points for the recovery tests. A test sets one point; when the writer reaches it the
// process _exit()s at once (no destructors, no flush), like a kill -9. Off in production: the
// point is None and the check is one relaxed atomic load.
#pragma once

#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace ombra::storage {

enum class CrashPoint : int {
  None = 0,
  InsertBeforeIntent,
  InsertAfterIntent,
  InsertBeforeSegmentRename,
  InsertAfterSegmentRename,
  InsertAfterUnlockedWrite,  // block file durable, the store lock not yet retaken (step 2 to 3)
  InsertAfterCatalog,
  InsertAfterCommit,
  DeleteBeforeRecord,
  DeleteAfterRecord,
  DeleteAfterCatalog,
  DropBeforeCatalog,
  DropAfterCatalog,    // collection gone from the catalog, files and log not yet cleaned
  DropAfterWalReset,   // log checkpointed, files not yet unlinked
  DropAfterFirstFile,  // some files unlinked
  // Block replacement (replace_segments), in order.
  ReplaceBeforeNewFile,     // log checkpointed, nothing written
  ReplaceAfterNewFile,      // new file durable, catalog untouched
  ReplaceAfterCatalog,      // catalog switched, old file still on disk
  ReplaceAfterFirstUnlink,  // first of several old files gone
  ReplaceAfterUnlink,       // all old files gone
  // Dropping fully tombstoned blocks (drop_dead_blocks), in order.
  CompactBeforeCatalog,
  CompactAfterCatalog,  // rows gone, files still on disk
  CompactAfterUnlink,
  // Blob store (put_blob / delete_blobs), in order.
  BlobBeforeRename,        // temp file durable, not renamed
  BlobAfterRename,         // blob file durable under its final name, catalog untouched
  BlobAfterCatalog,        // catalog switched, a replaced old file still on disk
  BlobDeleteAfterCatalog,  // rows gone, files still on disk
};

inline constexpr int kCrashExitCode = 42;

#ifdef OMBRA_ENABLE_TEST_HOOKS
// Test builds only (CMake option OMBRA_ENABLE_TEST_HOOKS, defined for every user of this header by
// the ombra_storage target): the environment variable OMBRA_CRASH_POINT=<enum name> arms a point
// when the process first reaches one, so the process tests can crash a real server. Release builds
// do not compile this and never read the variable.
inline int crash_point_from_env() {
  const char* env = std::getenv("OMBRA_CRASH_POINT");
  if (env == nullptr) return 0;
  static const struct {
    const char* name;
    CrashPoint point;
  } kNames[] = {
      {"InsertBeforeIntent", CrashPoint::InsertBeforeIntent},
      {"InsertAfterIntent", CrashPoint::InsertAfterIntent},
      {"InsertBeforeSegmentRename", CrashPoint::InsertBeforeSegmentRename},
      {"InsertAfterSegmentRename", CrashPoint::InsertAfterSegmentRename},
      {"InsertAfterUnlockedWrite", CrashPoint::InsertAfterUnlockedWrite},
      {"InsertAfterCatalog", CrashPoint::InsertAfterCatalog},
      {"InsertAfterCommit", CrashPoint::InsertAfterCommit},
      {"DeleteBeforeRecord", CrashPoint::DeleteBeforeRecord},
      {"DeleteAfterRecord", CrashPoint::DeleteAfterRecord},
      {"DeleteAfterCatalog", CrashPoint::DeleteAfterCatalog},
      {"DropBeforeCatalog", CrashPoint::DropBeforeCatalog},
      {"DropAfterCatalog", CrashPoint::DropAfterCatalog},
      {"DropAfterWalReset", CrashPoint::DropAfterWalReset},
      {"DropAfterFirstFile", CrashPoint::DropAfterFirstFile},
      {"ReplaceBeforeNewFile", CrashPoint::ReplaceBeforeNewFile},
      {"ReplaceAfterNewFile", CrashPoint::ReplaceAfterNewFile},
      {"ReplaceAfterCatalog", CrashPoint::ReplaceAfterCatalog},
      {"ReplaceAfterFirstUnlink", CrashPoint::ReplaceAfterFirstUnlink},
      {"ReplaceAfterUnlink", CrashPoint::ReplaceAfterUnlink},
      {"CompactBeforeCatalog", CrashPoint::CompactBeforeCatalog},
      {"CompactAfterCatalog", CrashPoint::CompactAfterCatalog},
      {"CompactAfterUnlink", CrashPoint::CompactAfterUnlink},
      {"BlobBeforeRename", CrashPoint::BlobBeforeRename},
      {"BlobAfterRename", CrashPoint::BlobAfterRename},
      {"BlobAfterCatalog", CrashPoint::BlobAfterCatalog},
      {"BlobDeleteAfterCatalog", CrashPoint::BlobDeleteAfterCatalog},
  };
  for (const auto& e : kNames)
    if (std::strcmp(env, e.name) == 0) return static_cast<int>(e.point);
  return 0;
}
#endif

inline std::atomic<int>& crash_point_storage() {
#ifdef OMBRA_ENABLE_TEST_HOOKS
  static std::atomic<int> p{crash_point_from_env()};
#else
  static std::atomic<int> p{0};
#endif
  return p;
}
inline void set_crash_point(CrashPoint p) { crash_point_storage().store(static_cast<int>(p)); }
inline void maybe_crash(CrashPoint p) {
  if (crash_point_storage().load(std::memory_order_relaxed) == static_cast<int>(p))
    _exit(kCrashExitCode);
}

// Disk-full injection for the tests. set_fs_fault(n, ENOSPC) makes the n-th instrumented I/O call
// after it fail once (write, fsync, rename, create in fsutil and the log, and the catalog
// commit); n counts calls from 1. A write fails after writing half its bytes, like a full disk.
// Off in production: the countdown is 0 and the check is one relaxed atomic load. To count the
// calls of a scenario, arm a large n and subtract fs_fault_remaining() afterwards.
inline std::atomic<long>& fs_fault_countdown() {
  static std::atomic<long> c{0};
  return c;
}
inline std::atomic<int>& fs_fault_errno() {
  static std::atomic<int> e{0};
  return e;
}
inline void set_fs_fault(long n, int err) {
  fs_fault_errno().store(err);
  fs_fault_countdown().store(n);
}
inline void clear_fs_fault() { fs_fault_countdown().store(0); }
inline long fs_fault_remaining() { return fs_fault_countdown().load(); }
// True when this call must fail (errno is set to the injected error).
inline bool fs_fault_hit() {
  if (fs_fault_countdown().load(std::memory_order_relaxed) <= 0) return false;
  if (fs_fault_countdown().fetch_sub(1) != 1) return false;
  errno = fs_fault_errno().load();
  return true;
}

// Slow-disk injection for the concurrency tests: every durable file write sleeps this many
// milliseconds before its fsync (0 = off, one relaxed atomic load).
inline std::atomic<int>& write_delay_ms_storage() {
  static std::atomic<int> d{0};
  return d;
}
inline void set_write_delay_ms(int ms) { write_delay_ms_storage().store(ms); }
inline void maybe_delay_write() {
  const int ms = write_delay_ms_storage().load(std::memory_order_relaxed);
  if (ms > 0) std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

}  // namespace ombra::storage
