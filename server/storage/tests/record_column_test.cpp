// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Record columns: the ORCL parser, the record file container, and their storage next to a block
// (insert, replace, delete, drop, recovery after a crash, blocks without a column). Synthetic bytes
// only; a column here is a header plus filler rows, never a sealed record.
#include "ombra/storage/record_column.hpp"

#include <sys/wait.h>
#include <unistd.h>

#include <filesystem>
#include <fstream>
#include <set>

#include "ombra/storage/backup.hpp"
#include "ombra/storage/failpoint.hpp"
#include "ombra/storage/fsutil.hpp"
#include "ombra/storage/store.hpp"
#include "ombra/storage/verify.hpp"
#include "test_util.hpp"

using namespace ombra::storage;
using namespace ombra::storage::testing;
namespace fs = std::filesystem;

namespace {

using Bytes = std::vector<std::uint8_t>;

Bytes make_column(std::uint32_t row_size, std::uint32_t n_rows, std::uint8_t fill,
                  std::uint16_t version = 1, std::uint16_t reserved = 0) {
  Bytes c{'O', 'R', 'C', 'L'};
  put_le(c, version, 2);
  put_le(c, reserved, 2);
  put_le(c, row_size, 4);
  put_le(c, n_rows, 4);
  c.insert(c.end(), static_cast<std::size_t>(row_size) * n_rows, fill);
  return c;
}

std::vector<Bytes> cols(std::uint8_t f) { return {column(f), column(f + 1)}; }

std::set<std::string> files_in(const std::string& dir) {
  std::set<std::string> s;
  for (auto& e : fs::directory_iterator(dir)) s.insert(e.path().filename().string());
  return s;
}

template <class F>
int run_child(const std::string& root, CrashPoint p, F fn) {
  pid_t pid = ::fork();
  CHECK(pid >= 0);
  if (pid == 0) {
    Store s(root, kOfhe);
    set_crash_point(p);
    fn(s);
    ::_exit(0);
  }
  int st = 0;
  CHECK(::waitpid(pid, &st, 0) == pid);
  CHECK(WIFEXITED(st));
  return WEXITSTATUS(st);
}

template <class E, class F>
bool throws_as(F fn) {
  try {
    fn();
  } catch (const E&) {
    return true;
  } catch (...) {
    return false;
  }
  return false;
}

// ---- parser -------------------------------------------------------------------------------------

void parser_accepts() {
  const auto i = parse_record_column(make_column(64, 3, 7));
  CHECK(i.row_size == 64 && i.n_rows == 3);
  const auto j = parse_record_column(make_column(1728, 5, 1));  // 27 * 64
  CHECK(j.row_size == 1728 && j.n_rows == 5);
  CHECK(parse_record_column(make_column(64, 0, 0)).n_rows == 0);  // like parse_column in pir.py
}

void parser_refuses() {
  const auto bad = [](const Bytes& b) {
    return throws_as<RecordColumnError>([&] { parse_record_column(b); });
  };
  CHECK(bad({}));
  for (std::size_t n = 1; n < kRecordColumnHeaderLen; ++n) {
    Bytes b = make_column(64, 1, 0);
    b.resize(n);
    CHECK(bad(b));  // truncated header
  }
  Bytes m = make_column(64, 1, 0);
  m[0] = 'X';
  CHECK(bad(m));                            // magic
  CHECK(bad(make_column(64, 1, 0, 2)));     // version 2
  CHECK(bad(make_column(64, 1, 0, 0)));     // version 0
  CHECK(bad(make_column(64, 1, 0, 1, 1)));  // reserved != 0
  CHECK(bad(make_column(0, 0, 0)));         // row size 0
  Bytes r65{'O', 'R', 'C', 'L'};
  put_le(r65, 1, 2);
  put_le(r65, 0, 2);
  put_le(r65, 65, 4);
  put_le(r65, 1, 4);
  r65.insert(r65.end(), 65, 0);
  CHECK(bad(r65));  // row size not a multiple of 64
  Bytes shortb = make_column(64, 2, 0);
  shortb.pop_back();
  CHECK(bad(shortb));  // body shorter than the header says
  Bytes longb = make_column(64, 2, 0);
  longb.push_back(0);
  CHECK(bad(longb));  // body longer
  // n_rows * row_size beyond 32 bits does not wrap around into a match.
  Bytes huge{'O', 'R', 'C', 'L'};
  put_le(huge, 1, 2);
  put_le(huge, 0, 2);
  put_le(huge, 0xFFFFFFC0u, 4);
  put_le(huge, 0xFFFFFFFFu, 4);
  CHECK(bad(huge));
}

void file_container() {
  TempDir d;
  const Bytes col = make_column(128, 4, 9);
  const std::string path = d.path + "/x.orec";
  write_record_file(path, col);
  CHECK(read_record_file(path) == col);
  Bytes f = read_file(path);
  CHECK(f[0] == 'O' && f[1] == 'R' && f[2] == 'E' && f[3] == 'C');
  CHECK(get_le(f.data() + 4, 2) == kRecordFileVersion);
  for (const std::size_t at : {std::size_t{0}, std::size_t{4}, std::size_t{7}, f.size() - 1}) {
    Bytes g = f;
    g[at] ^= 1;  // magic, version, CRC, body
    CHECK(throws_as<RecordColumnError>([&] { decode_record_file(g); }));
  }
  Bytes t = f;
  t.resize(5);
  CHECK(throws_as<RecordColumnError>([&] { decode_record_file(t); }));
  // A container whose CRC is right but whose column is malformed is refused too.
  Bytes badcol = make_column(64, 1, 0);
  badcol.pop_back();
  CHECK(throws_as<RecordColumnError>([&] { decode_record_file(encode_record_file(badcol)); }));
  CHECK(record_file_name("s0000000000000007.oseg") == "s0000000000000007.orec");
  CHECK(is_valid_record_name("s0000000000000007.orec"));
  CHECK(!is_valid_record_name("s0000000000000007.oseg"));
  CHECK(!is_valid_record_name("../etc/passwd"));
  CHECK_THROWS(record_file_name("nope"));
}

// ---- storage ------------------------------------------------------------------------------------

void round_trip_and_reopen() {
  TempDir d;
  const Bytes rc = make_column(128, 3, 5);
  std::string seg;
  {
    Store s(d.path, kOfhe);
    s.create_collection(spec());
    seg = s.insert("docs", 1, {"a", "b", "c"}, cols(1), nullptr, &rc);
    Bytes got;
    CHECK(s.get_record_column("docs", seg, &got) && got == rc);
    CHECK(files_in(d.path + "/records") == std::set<std::string>{record_file_name(seg)});
    // The block itself is unchanged by the column.
    CHECK(s.read_segment("docs", seg).ids.size() == 3);
    CHECK(s.disk_bytes("docs") > rc.size());
  }
  Store s(d.path, kOfhe);  // reopen: recovery keeps the file
  Bytes got;
  CHECK(s.get_record_column("docs", seg, &got) && got == rc);
  CHECK(s.recovery_stats().orphans_removed == 0);
}

void insert_refusals_leave_nothing() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  const auto nothing = [&] {
    CHECK(files_in(d.path + "/records").empty());
    CHECK(s.segments("docs").empty());
    CHECK(files_in(d.path + "/segments").empty());
  };
  // Row count differs from the ids.
  CHECK(throws_as<RecordColumnError>([&] {
    s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, &(const Bytes&)make_column(64, 3, 1));
  }));
  nothing();
  // Malformed header.
  Bytes bad = make_column(64, 2, 1);
  bad[0] = 'Z';
  CHECK(throws_as<RecordColumnError>(
      [&] { s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, &bad); }));
  nothing();
  // A center block has no records (cluster id == cluster_count).
  const Bytes two = make_column(64, 2, 1);
  CHECK(throws_as<RecordColumnError>(
      [&] { s.insert("docs", 4, {"a", "b"}, cols(1), nullptr, &two); }));
  nothing();
  // A bad column envelope after a good record column: the record file is removed again.
  std::vector<Bytes> badenv{column(1), Bytes{1, 2, 3}};
  CHECK_THROWS(s.insert("docs", 0, {"a", "b"}, badenv, nullptr, &two));
  nothing();
}

void blocks_without_column_work_as_before() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  const auto seg = s.insert("docs", 0, {"a", "b"}, cols(1));
  Bytes got;
  CHECK(!s.get_record_column("docs", seg, &got));
  CHECK(files_in(d.path + "/records").empty());
  CHECK(s.read_segment("docs", seg).ids.size() == 2);
  // A plain repeat is still a replay.
  bool replayed = false;
  CHECK(s.insert("docs", 0, {"a", "b"}, cols(1), &replayed) == seg && replayed);
  // A scope that reaches it reports the missing column.
  CHECK(throws_as<RecordColumnMissing>([&] { s.read_record_columns("docs", {0}, false); }));
  CHECK(throws_as<RecordColumnMissing>([&] { s.read_record_columns("docs", {}, true); }));
  // A scope that does not reach it is fine.
  CHECK(s.read_record_columns("docs", {1}, false).n_rows == 0);
  // Replacement without a column works, and none appears.
  const auto neu = s.replace_segments("docs", {seg}, SegmentData{0, {"b", "a"}, cols(3)});
  CHECK(neu != seg && !s.get_record_column("docs", neu, &got));
  // Delete and drop of such blocks as before.
  s.remove("docs", {"a", "b"});
  CHECK(s.drop_dead_blocks("docs") == 1);
  CHECK(files_in(d.path + "/records").empty());
}

void repeat_must_match_the_column() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  const Bytes rc = make_column(64, 2, 3), other = make_column(64, 2, 4);
  const auto seg = s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, &rc);
  bool replayed = false;
  CHECK(s.insert("docs", 0, {"a", "b"}, cols(1), &replayed, &rc) == seg && replayed);
  CHECK(files_in(d.path + "/records").size() == 1);
  CHECK(throws_as<std::runtime_error>(
      [&] { s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, &other); }));
  CHECK(throws_as<std::runtime_error>([&] { s.insert("docs", 0, {"a", "b"}, cols(1)); }));
  Bytes got;
  CHECK(s.get_record_column("docs", seg, &got) && got == rc);  // unchanged
  CHECK(files_in(d.path + "/records").size() == 1);
  // And the other way round: stored without, repeated with.
  const auto seg2 = s.insert("docs", 1, {"c"}, cols(5));
  const Bytes one = make_column(64, 1, 1);
  CHECK(throws_as<std::runtime_error>([&] { s.insert("docs", 1, {"c"}, cols(5), nullptr, &one); }));
  CHECK(!s.get_record_column("docs", seg2, &got));
}

void scope_read() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());  // clusters 0..3 are data, 4 is the center
  // Insert out of cluster order, two blocks in cluster 2.
  const auto b2a =
      s.insert("docs", 2, {"p", "q"}, cols(1), nullptr, &(const Bytes&)make_column(128, 2, 0x22));
  const auto b0 =
      s.insert("docs", 0, {"r"}, cols(2), nullptr, &(const Bytes&)make_column(128, 1, 0x00));
  const auto b2b =
      s.insert("docs", 2, {"t"}, cols(3), nullptr, &(const Bytes&)make_column(128, 1, 0x23));
  s.insert("docs", 4, {"center"}, {column(9)});  // center block: never in a scope
  const auto all = s.read_record_columns("docs", {}, true);
  CHECK(all.n_rows == 4 && all.row_size == 128 && all.spans.size() == 3);
  CHECK(all.spans[0].cluster == 0 && all.spans[0].segment == b0 && all.spans[0].first_row == 0);
  CHECK(all.spans[1].cluster == 2 && all.spans[1].segment == b2a && all.spans[1].first_row == 1 &&
        all.spans[1].n_rows == 2);
  CHECK(all.spans[2].segment == b2b && all.spans[2].first_row == 3 && all.spans[2].n_rows == 1);
  // It is itself a valid column, rows in that order.
  const auto info = parse_record_column(all.column);
  CHECK(info.n_rows == 4 && info.row_size == 128);
  CHECK(all.column[kRecordColumnHeaderLen] == 0x00);
  CHECK(all.column[kRecordColumnHeaderLen + 128] == 0x22);
  CHECK(all.column[kRecordColumnHeaderLen + 3 * 128] == 0x23);
  // Listed clusters: the order is the cluster order, not the request order.
  const auto some = s.read_record_columns("docs", {2, 0}, false);
  CHECK(some.column == all.column);
  const auto two = s.read_record_columns("docs", {2}, false);
  CHECK(two.n_rows == 3 && two.spans.size() == 2 && two.spans[0].first_row == 0);
  // A cluster with no block gives zero rows, and an empty scope is a valid empty column.
  const auto none = s.read_record_columns("docs", {3}, false);
  CHECK(none.n_rows == 0 && none.spans.empty() && parse_record_column(none.column).n_rows == 0);
  // Bad scopes.
  CHECK_THROWS(s.read_record_columns("docs", {4}, false));  // the center id
  CHECK_THROWS(s.read_record_columns("docs", {99}, false));
  CHECK_THROWS(s.read_record_columns("docs", {1, 1}, false));
  CHECK_THROWS(s.read_record_columns("nope", {}, true));
  // Different row sizes in one scope are refused.
  s.insert("docs", 3, {"u"}, cols(4), nullptr, &(const Bytes&)make_column(192, 1, 1));
  CHECK(throws_as<RecordColumnMissing>([&] { s.read_record_columns("docs", {}, true); }));
  CHECK(s.read_record_columns("docs", {3}, false).row_size == 192);
}

void replace_delete_drop() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  const auto a = s.insert("docs", 0, {"a1", "a2", "a3"}, cols(1), nullptr,
                          &(const Bytes&)make_column(64, 3, 1));
  const auto c =
      s.insert("docs", 0, {"a4", "a5"}, cols(3), nullptr, &(const Bytes&)make_column(64, 2, 2));
  s.remove("docs", {"a2"});
  CHECK(files_in(d.path + "/records").size() == 2);
  // The repack drops the tombstoned slot; the new column has one row per new slot.
  const Bytes rc = make_column(64, 4, 9);
  CHECK(throws_as<RecordColumnError>([&] {
    s.replace_segments("docs", {a, c}, SegmentData{0, {"a3", "a4", "a1", "a5"}, cols(9)},
                       &(const Bytes&)make_column(64, 5, 9));
  }));
  CHECK(files_in(d.path + "/records").size() == 2);  // refused: nothing changed
  CHECK(files_in(d.path + "/segments").size() == 2);
  const auto neu =
      s.replace_segments("docs", {a, c}, SegmentData{0, {"a3", "a4", "a1", "a5"}, cols(9)}, &rc);
  CHECK(files_in(d.path + "/records") == std::set<std::string>{record_file_name(neu)});
  Bytes got;
  CHECK(s.get_record_column("docs", neu, &got) && got == rc);
  CHECK(!s.get_record_column("docs", a, &got));  // gone with its block
  // All vectors deleted: the dead block and its column go.
  s.remove("docs", {"a1", "a3", "a4", "a5"});
  CHECK(s.drop_dead_blocks("docs") == 1);
  CHECK(files_in(d.path + "/records").empty());
  // A collection drop removes the columns of all its blocks.
  s.insert("docs", 1, {"z"}, cols(1), nullptr, &(const Bytes&)make_column(64, 1, 1));
  CHECK(files_in(d.path + "/records").size() == 1);
  CHECK(s.drop_collection("docs"));
  CHECK(files_in(d.path + "/records").empty());
}

void pinned_replace_keeps_the_old_column_until_release() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  const auto a = s.insert("docs", 0, {"a"}, cols(1), nullptr, &(const Bytes&)make_column(64, 1, 1));
  {
    auto snap = s.snapshot("docs");  // a search holds the block
    s.replace_segments("docs", {a}, SegmentData{0, {"a"}, cols(2)},
                       &(const Bytes&)make_column(64, 1, 2));
    CHECK(files_in(d.path + "/records").count(record_file_name(a)) == 1);  // deferred
    CHECK(files_in(d.path + "/records").size() == 2);
  }
  CHECK(files_in(d.path + "/records").size() == 1);  // released: gone
}

// ---- crashes ------------------------------------------------------------------------------------

void crash_before_commit_leaves_nothing() {
  // Every crash point before the catalog commit: the block is absent, and no record file stays.
  for (const CrashPoint p :
       {CrashPoint::InsertAfterRecordFile, CrashPoint::InsertBeforeSegmentRename,
        CrashPoint::InsertAfterSegmentRename, CrashPoint::InsertAfterUnlockedWrite,
        CrashPoint::InsertBeforeIntent, CrashPoint::InsertAfterIntent}) {
    TempDir d;
    {
      Store s(d.path, kOfhe);
      s.create_collection(spec());
      s.insert("docs", 0, {"keep"}, cols(1), nullptr, &(const Bytes&)make_column(64, 1, 1));
    }
    const int code = run_child(d.path, p, [](Store& s) {
      s.insert("docs", 1, {"x", "y"}, cols(5), nullptr, &(const Bytes&)make_column(64, 2, 5));
    });
    CHECK(code == kCrashExitCode);
    Store s(d.path, kOfhe);
    CHECK(s.segments("docs").size() == 1);
    CHECK(files_in(d.path + "/segments").size() == 1);
    CHECK(files_in(d.path + "/records").size() == 1);  // only the acknowledged block's
    const auto cols_all = s.read_record_columns("docs", {}, true);
    CHECK(cols_all.n_rows == 1);
    CHECK(s.recovery_stats().orphans_removed >= 1);
  }
}

void crash_after_commit_keeps_the_block_and_column() {
  for (const CrashPoint p : {CrashPoint::InsertAfterCatalog, CrashPoint::InsertAfterCommit}) {
    TempDir d;
    {
      Store s(d.path, kOfhe);
      s.create_collection(spec());
    }
    CHECK(run_child(d.path, p, [](Store& s) {
            s.insert("docs", 1, {"x", "y"}, cols(5), nullptr, &(const Bytes&)make_column(64, 2, 5));
          }) == kCrashExitCode);
    Store s(d.path, kOfhe);
    CHECK(s.segments("docs").size() == 1);
    const auto all = s.read_record_columns("docs", {}, true);
    CHECK(all.n_rows == 2 && all.column[kRecordColumnHeaderLen] == 5);
    CHECK(files_in(d.path + "/records").size() == 1);
  }
}

void crash_during_replace() {
  struct Case {
    CrashPoint p;
    bool replaced;  // the new block is in the catalog after recovery
  };
  for (const Case c :
       {Case{CrashPoint::ReplaceBeforeNewFile, false},
        Case{CrashPoint::ReplaceAfterRecordFile, false},
        Case{CrashPoint::ReplaceAfterNewFile, false}, Case{CrashPoint::ReplaceAfterCatalog, true},
        Case{CrashPoint::ReplaceAfterUnlink, true}}) {
    TempDir d;
    std::string old_seg;
    {
      Store s(d.path, kOfhe);
      s.create_collection(spec());
      old_seg =
          s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, &(const Bytes&)make_column(64, 2, 1));
    }
    CHECK(run_child(d.path, c.p, [&](Store& s) {
            s.replace_segments("docs", {old_seg}, SegmentData{0, {"b", "a"}, cols(7)},
                               &(const Bytes&)make_column(64, 2, 7));
          }) == kCrashExitCode);
    Store s(d.path, kOfhe);
    const auto segs = s.segments("docs");
    CHECK(segs.size() == 1);
    CHECK((segs[0].file != old_seg) == c.replaced);
    // Exactly one record file, the surviving block's, and it is that block's own column.
    CHECK(files_in(d.path + "/records") == std::set<std::string>{record_file_name(segs[0].file)});
    CHECK(files_in(d.path + "/segments").size() == 1);
    Bytes got;
    CHECK(s.get_record_column("docs", segs[0].file, &got));
    CHECK(got[kRecordColumnHeaderLen] == (c.replaced ? 7 : 1));
  }
}

void crash_during_drop() {
  for (const CrashPoint p : {CrashPoint::DropAfterCatalog, CrashPoint::DropAfterWalReset,
                             CrashPoint::DropAfterFirstFile}) {
    TempDir d;
    {
      Store s(d.path, kOfhe);
      s.create_collection(spec());
      s.insert("docs", 0, {"a"}, cols(1), nullptr, &(const Bytes&)make_column(64, 1, 1));
      s.insert("docs", 1, {"b"}, cols(1), nullptr, &(const Bytes&)make_column(64, 1, 2));
    }
    CHECK(run_child(d.path, p, [](Store& s) { s.drop_collection("docs"); }) == kCrashExitCode);
    Store s(d.path, kOfhe);
    CHECK(s.list_collections().empty());
    CHECK(files_in(d.path + "/records").empty());
    CHECK(files_in(d.path + "/segments").empty());
  }
}

void older_data_dir_without_records_dir() {
  TempDir d;
  {
    Store s(d.path, kOfhe);
    s.create_collection(spec());
    s.insert("docs", 0, {"a"}, cols(1));
  }
  fs::remove_all(d.path + "/records");  // a directory made before record columns existed
  Store s(d.path, kOfhe);
  CHECK(fs::is_directory(d.path + "/records"));
  CHECK(s.segments("docs").size() == 1);
}

// ---- offline tools ------------------------------------------------------------------------------

void verify_and_backup() {
  TempDir d, out;
  std::string seg;
  {
    Store s(d.path, kOfhe);
    s.create_collection(spec());
    seg = s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, &(const Bytes&)make_column(64, 2, 1));
    s.insert("docs", 1, {"c"}, cols(2));  // a block without a column
  }
  VerifyReport r;
  verify_data_dir(d.path, r);
  CHECK(r.clean() && r.record_files == 1);
  // The backup carries the column, and the copy verifies and opens.
  BackupReport rep;
  backup_data_dir(d.path, out.path + "/b", rep);
  CHECK(rep.segments == 2 && rep.records == 1);
  CHECK(files_in(out.path + "/b/records") == std::set<std::string>{record_file_name(seg)});
  {
    Store restored(out.path + "/b", kOfhe);
    Bytes got;
    CHECK(restored.get_record_column("docs", seg, &got) && got == make_column(64, 2, 1));
  }
  // A damaged column and a stray file are reported.
  const std::string rpath = d.path + "/records/" + record_file_name(seg);
  Bytes f = read_file(rpath);
  f.back() ^= 1;
  {
    std::ofstream(rpath, std::ios::binary | std::ios::trunc)
        .write(reinterpret_cast<const char*>(f.data()), static_cast<std::streamsize>(f.size()));
  }
  std::ofstream(d.path + "/records/stray.orec") << "x";
  VerifyReport bad;
  verify_data_dir(d.path, bad);
  bool corrupt = false, orphan = false;
  for (const auto& x : bad.findings) {
    corrupt |= x.kind == "record" && x.severity == Severity::Corrupt;
    orphan |= x.kind == "orphan" && x.file == "stray.orec";
  }
  CHECK(corrupt && orphan && !bad.clean());
  CHECK_THROWS(backup_data_dir(d.path, out.path + "/c", rep));  // a corrupt source fails
}

}  // namespace

int main() {
  parser_accepts();
  parser_refuses();
  file_container();
  round_trip_and_reopen();
  insert_refusals_leave_nothing();
  blocks_without_column_work_as_before();
  repeat_must_match_the_column();
  scope_read();
  replace_delete_drop();
  pinned_replace_keeps_the_old_column_until_release();
  crash_before_commit_leaves_nothing();
  crash_after_commit_keeps_the_block_and_column();
  crash_during_replace();
  crash_during_drop();
  older_data_dir_without_records_dir();
  verify_and_backup();
  std::puts("record_column_test ok");
  return 0;
}
