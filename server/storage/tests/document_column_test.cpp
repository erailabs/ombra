// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Document columns (ODCL) stored next to a block: the parser, the document file container, insert,
// repeat, replace, scope reads, reopen, verify and backup. Synthetic bytes only.
#include <filesystem>
#include <fstream>
#include <set>

#include "ombra/storage/backup.hpp"
#include "ombra/storage/fsutil.hpp"
#include "ombra/storage/record_column.hpp"
#include "ombra/storage/store.hpp"
#include "ombra/storage/verify.hpp"
#include "test_util.hpp"

using namespace ombra::storage;
using namespace ombra::storage::testing;
namespace fs = std::filesystem;

namespace {

using Bytes = std::vector<std::uint8_t>;

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

Bytes doc_column(std::uint32_t cls, std::uint32_t n_rows, std::uint8_t fill,
                 std::uint32_t row_size = 0, std::uint16_t version = 1) {
  if (row_size == 0) row_size = doc_class_row_size(cls);
  Bytes c{'O', 'D', 'C', 'L'};
  put_le(c, version, 2);
  put_le(c, cls, 2);
  put_le(c, row_size, 4);
  put_le(c, n_rows, 4);
  for (std::uint32_t r = 0; r < n_rows; ++r)
    c.insert(c.end(), row_size, static_cast<std::uint8_t>(fill + r));
  return c;
}

Bytes rec_column(std::uint32_t n_rows, std::uint8_t fill) {
  Bytes c{'O', 'R', 'C', 'L'};
  put_le(c, 2, 2);
  put_le(c, 0, 2);
  put_le(c, 64, 4);
  put_le(c, n_rows, 4);
  c.insert(c.end(), static_cast<std::size_t>(64) * n_rows, fill);
  return c;
}

std::vector<Bytes> cols(std::uint8_t f) { return {column(f), column(f + 1)}; }

std::set<std::string> files_in(const std::string& dir) {
  std::set<std::string> s;
  for (auto& e : fs::directory_iterator(dir)) s.insert(e.path().filename().string());
  return s;
}

void parser() {
  CHECK(parse_doc_column(doc_column(1, 2, 3)).row_size == 16384);
  CHECK(parse_doc_column(doc_column(2, 1, 3)).row_size == 262144);
  CHECK(parse_doc_column(doc_column(1, 0, 0)).n_rows == 0);
  const auto bad = [](const Bytes& b) {
    return throws_as<RecordColumnError>([&] { parse_doc_column(b); });
  };
  CHECK(bad({}));
  CHECK(bad(doc_column(3, 1, 0, 16384)));     // unknown class
  CHECK(bad(doc_column(0, 1, 0, 16384)));     // unknown class
  CHECK(bad(doc_column(1, 1, 0, 262144)));    // class 1 with the row size of class 2
  CHECK(bad(doc_column(1, 1, 0, 16384, 2)));  // version 2
  Bytes m = doc_column(1, 1, 0);
  m[0] = 'X';
  CHECK(bad(m));
  Bytes shortb = doc_column(1, 2, 0);
  shortb.pop_back();
  CHECK(bad(shortb));
  // A record column is not a document column.
  CHECK(bad(rec_column(1, 0)));
  // The record parser takes versions 1 and 2 and nothing else.
  CHECK(parse_record_column(rec_column(1, 0)).version == 2);
}

void file_container() {
  DocumentColumns d;
  d[1] = doc_column(1, 2, 1);
  d[2] = doc_column(2, 1, 9);
  const Bytes f = encode_doc_file(d);
  CHECK(f[0] == 'O' && f[1] == 'D' && f[2] == 'O' && f[3] == 'C');
  CHECK(get_le(f.data() + 4, 2) == kDocFileVersion);
  CHECK(decode_doc_file(f) == d);
  for (const std::size_t at : {0u, 4u, 6u, 12u, 30u}) {
    Bytes g = f;
    g[at] ^= 1;
    CHECK(throws_as<RecordColumnError>([&] { decode_doc_file(g); }));
  }
  CHECK(throws_as<RecordColumnError>([&] { decode_doc_file(Bytes(f.begin(), f.begin() + 9)); }));
  // Classes must ascend in the file.
  Bytes swapped{'O', 'D', 'O', 'C'};
  put_le(swapped, 1, 2);
  Bytes body = d[2];
  body.insert(body.end(), d[1].begin(), d[1].end());
  put_le(swapped, crc32(body.data(), body.size()), 4);
  swapped.insert(swapped.end(), body.begin(), body.end());
  CHECK(throws_as<RecordColumnError>([&] { decode_doc_file(swapped); }));
  CHECK(doc_file_name("s0000000000000007.oseg") == "s0000000000000007.odoc");
  CHECK(is_valid_doc_name("s0000000000000007.odoc"));
  CHECK(!is_valid_doc_name("s0000000000000007.orec"));
  CHECK(!is_valid_doc_name("../etc/passwd"));
}

void round_trip_replay_and_reopen() {
  TempDir d;
  const Bytes rc = rec_column(3, 5);
  DocumentColumns docs;
  docs[1] = doc_column(1, 2, 1);
  docs[2] = doc_column(2, 3, 20);
  std::string seg;
  {
    Store s(d.path, kOfhe);
    s.create_collection(spec());
    seg = s.insert("docs", 1, {"a", "b", "c"}, cols(1), nullptr, &rc, &docs);
    CHECK(files_in(d.path + "/records") ==
          (std::set<std::string>{record_file_name(seg), doc_file_name(seg)}));
    CHECK(s.disk_bytes("docs") > docs[2].size());
    // A repeat with the same columns is a replay; without, or with others, it is not.
    bool replayed = false;
    CHECK(s.insert("docs", 1, {"a", "b", "c"}, cols(1), &replayed, &rc, &docs) == seg && replayed);
    CHECK(throws_as<std::runtime_error>(
        [&] { s.insert("docs", 1, {"a", "b", "c"}, cols(1), nullptr, &rc); }));
    DocumentColumns other = docs;
    other[1] = doc_column(1, 2, 7);
    CHECK(throws_as<std::runtime_error>(
        [&] { s.insert("docs", 1, {"a", "b", "c"}, cols(1), nullptr, &rc, &other); }));
    DocumentColumns fewer;
    fewer[1] = docs[1];
    CHECK(throws_as<std::runtime_error>(
        [&] { s.insert("docs", 1, {"a", "b", "c"}, cols(1), nullptr, &rc, &fewer); }));
    CHECK(files_in(d.path + "/records").size() == 2);
  }
  Store s(d.path, kOfhe);  // reopen: recovery keeps both files
  CHECK(s.recovery_stats().orphans_removed == 0);
  const auto one = s.read_document_columns("docs", {}, true, 1);
  CHECK(one.collection_has_class && one.n_rows == 2 && one.row_size == 16384);
  CHECK(one.rows.size() == 2u * 16384 && one.rows[0] == 1 && one.rows[16384] == 2);
  const auto two = s.read_document_columns("docs", {1}, false, 2);
  CHECK(two.n_rows == 3 && two.rows.size() == 3u * 262144 && two.rows[2 * 262144] == 22);
  CHECK(two.spans.size() == 1 && two.spans[0].first_row == 0 && two.spans[0].n_rows == 3);
}

void refusals_leave_nothing() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  const auto nothing = [&] {
    CHECK(files_in(d.path + "/records").empty());
    CHECK(s.segments("docs").empty());
  };
  DocumentColumns many;
  many[1] = doc_column(1, 3, 1);  // 3 rows, 2 ids
  CHECK(throws_as<RecordColumnError>(
      [&] { s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, nullptr, &many); }));
  nothing();
  DocumentColumns wrong_class;
  wrong_class[2] = doc_column(1, 1, 1);  // a class 1 column under class 2
  CHECK(throws_as<RecordColumnError>(
      [&] { s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, nullptr, &wrong_class); }));
  nothing();
  DocumentColumns center;
  center[1] = doc_column(1, 1, 1);
  CHECK(throws_as<RecordColumnError>(
      [&] { s.insert("docs", 4, {"a"}, {column(9)}, nullptr, nullptr, &center); }));
  nothing();
  CHECK_THROWS(
      s.insert("docs", 0, {"a", "b"}, {column(1), Bytes{1, 2, 3}}, nullptr, nullptr, &center));
  nothing();
}

void scope_read() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());  // clusters 0..3 are data, 4 is the center
  // No document anywhere: the collection has none of either class.
  s.insert("docs", 0, {"z"}, cols(9));
  CHECK(!s.read_document_columns("docs", {}, true, 1).collection_has_class);
  DocumentColumns a, b, c;
  a[1] = doc_column(1, 2, 10);
  b[1] = doc_column(1, 1, 50);
  b[2] = doc_column(2, 1, 60);
  c[1] = doc_column(1, 1, 80);
  const auto s2a = s.insert("docs", 2, {"p", "q"}, cols(1), nullptr, nullptr, &a);
  const auto s0 = s.insert("docs", 0, {"r"}, cols(2), nullptr, nullptr, &b);
  const auto s2b = s.insert("docs", 2, {"t"}, cols(3), nullptr, nullptr, &c);
  const auto all = s.read_document_columns("docs", {}, true, 1);
  // Order: cluster ascending, then creation order; "z" (cluster 0, no column) counts zero rows.
  CHECK(all.collection_has_class && all.n_rows == 4 && all.spans.size() == 4);
  CHECK(all.spans[0].n_rows == 0);
  CHECK(all.spans[1].segment == s0 && all.spans[1].first_row == 0 && all.spans[1].n_rows == 1);
  CHECK(all.spans[2].segment == s2a && all.spans[2].first_row == 1 && all.spans[2].n_rows == 2);
  CHECK(all.spans[3].segment == s2b && all.spans[3].first_row == 3);
  CHECK(all.rows[0] == 50 && all.rows[16384] == 10 && all.rows[2 * 16384] == 11 &&
        all.rows[3 * 16384] == 80);
  // Class 2 only in one block.
  const auto big = s.read_document_columns("docs", {}, true, 2);
  CHECK(big.n_rows == 1 && big.rows[0] == 60);
  // A scope that reaches no row of the class still knows the collection has some.
  const auto none = s.read_document_columns("docs", {3}, false, 1);
  CHECK(none.collection_has_class && none.n_rows == 0 && none.spans.empty());
  const auto only_z = s.read_document_columns("docs", {1}, false, 2);
  CHECK(only_z.collection_has_class && only_z.n_rows == 0);
  CHECK(throws_as<std::runtime_error>([&] { s.read_document_columns("docs", {9}, false, 1); }));
  CHECK(throws_as<std::runtime_error>([&] { s.read_document_columns("docs", {}, true, 3); }));
}

void replace_removes_old_columns() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());
  DocumentColumns a;
  a[1] = doc_column(1, 2, 10);
  const auto seg = s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, nullptr, &a);
  CHECK(files_in(d.path + "/records").count(doc_file_name(seg)) == 1);
  DocumentColumns n;
  n[2] = doc_column(2, 1, 33);
  const auto neu =
      s.replace_segments("docs", {seg}, SegmentData{0, {"b", "a"}, cols(3)}, nullptr, &n);
  CHECK(files_in(d.path + "/records") == std::set<std::string>{doc_file_name(neu)});
  CHECK(!s.read_document_columns("docs", {}, true, 1).collection_has_class);
  CHECK(s.read_document_columns("docs", {}, true, 2).n_rows == 1);
  // A replacement without columns leaves the new block with none.
  const auto bare = s.replace_segments("docs", {neu}, SegmentData{0, {"a", "b"}, cols(5)});
  CHECK(files_in(d.path + "/records").empty() && bare != neu);
  // Delete and drop remove the file with the block.
  const auto again = s.insert("docs", 1, {"c"}, cols(7), nullptr, nullptr, &n);
  CHECK(files_in(d.path + "/records").count(doc_file_name(again)) == 1);
  s.remove("docs", {"c"});
  CHECK(s.drop_dead_blocks("docs") == 1);
  CHECK(files_in(d.path + "/records").empty());
}

void verify_and_backup() {
  TempDir d, out;
  std::string seg;
  DocumentColumns docs;
  docs[1] = doc_column(1, 1, 5);
  {
    Store s(d.path, kOfhe);
    s.create_collection(spec());
    seg = s.insert("docs", 0, {"a", "b"}, cols(1), nullptr, nullptr, &docs);
  }
  VerifyReport r;
  verify_data_dir(d.path, r);
  CHECK(r.clean());
  BackupReport rep;
  backup_data_dir(d.path, out.path + "/b", rep);
  CHECK(files_in(out.path + "/b/records") == std::set<std::string>{doc_file_name(seg)});
  {
    Store restored(out.path + "/b", kOfhe);
    CHECK(restored.read_document_columns("docs", {}, true, 1).n_rows == 1);
  }
  // A damaged file is reported; a stray one is an orphan.
  const std::string path = d.path + "/records/" + doc_file_name(seg);
  Bytes f = read_file(path);
  f.back() ^= 1;
  {
    std::ofstream(path, std::ios::binary | std::ios::trunc)
        .write(reinterpret_cast<const char*>(f.data()), static_cast<std::streamsize>(f.size()));
  }
  std::ofstream(d.path + "/records/stray.odoc") << "x";
  VerifyReport bad;
  verify_data_dir(d.path, bad);
  bool corrupt = false, orphan = false;
  for (const auto& x : bad.findings) {
    corrupt |= x.kind == "document" && x.severity == Severity::Corrupt;
    orphan |= x.kind == "orphan" && x.file == "stray.odoc";
  }
  CHECK(corrupt && orphan && !bad.clean());
  CHECK_THROWS(backup_data_dir(d.path, out.path + "/c", rep));
}

}  // namespace

int main() {
  parser();
  file_container();
  round_trip_replay_and_reopen();
  refusals_leave_nothing();
  scope_read();
  replace_removes_old_columns();
  verify_and_backup();
  std::puts("document_column_test ok");
  return 0;
}
