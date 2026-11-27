// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Record column: the sealed re-rank rows of one block, one row per slot, for private record
// retrieval. The server never opens a row (AES-GCM under a key only the client has); it checks the
// header, stores the bytes with the block and concatenates the columns of a scope for one call.
//
// Column layout (little endian, versions 1 and 2), the client's `parse_column` in
// client/ombra/pir.py. The version names the layout of the rows (a collection made before the
// metadata slot has version 1); the server never opens a row, so both are stored and joined alike:
//   0   4  magic "ORCL"
//   4   2  version (u16, 1 or 2)
//   6   2  reserved (u16, 0)
//   8   4  row size in bytes (u32, a positive multiple of 64)
//   12  4  number of rows (u32)
//   16  .  rows, n_rows x row_size bytes
//
// File on disk, records/<segment stem>.orec, container version 1 (the column is stored verbatim):
//   0   4  magic "OREC"
//   4   2  container version (u16, 1)
//   6   4  CRC32 of the body
//   10  .  body: the column, as above
// A block's record file is named after its segment (s<16 hex>.oseg -> s<16 hex>.orec), is written
// durably before the segment is, and is removed with it. A block with no record file has no column.
//
// Document columns (private fetch of documents): per class, the stored document blobs of the
// block's vectors of that class, in block order, opaque. Column layout (little endian, version 1),
// the client's `parse_doc_column`:
//   0   4  magic "ODCL"
//   4   2  version (u16, 1)
//   6   2  document class (u16): 1 (row size 16384) or 2 (row size 262144)
//   8   4  row size in bytes (u32, the class size)
//   12  4  number of rows (u32)
//   16  .  rows
// File on disk, records/<segment stem>.odoc, container version 1: the columns of the block, class
// ascending, back to back (each is self-delimiting):
//   0   4  magic "ODOC"
//   4   2  container version (u16, 1)
//   6   4  CRC32 of the body
//   10  .  body: one or two ODCL columns
// Written durably next to the record file, before the segment, and removed with it. A block with no
// .odoc file has no document column of any class.
#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace ombra::storage {

inline constexpr std::uint16_t kRecordColumnMinVersion = 1;
inline constexpr std::uint16_t kRecordColumnVersion = 2;  // the newest; 1 is still accepted
inline constexpr std::uint16_t kDocColumnVersion = 1;
inline constexpr std::uint16_t kDocFileVersion = 1;
inline constexpr std::uint32_t kDocClass16K = 1;
inline constexpr std::uint32_t kDocClass256K = 2;
inline constexpr std::uint32_t kDocClass16KBytes = 16384;
inline constexpr std::uint32_t kDocClass256KBytes = 262144;
// Largest document column of one class: a full block (8192 slots) of 256 KiB documents.
inline constexpr std::uint64_t kMaxDocumentColumnBytes =
    std::uint64_t{8192} * kDocClass256KBytes + 16;
inline constexpr std::uint16_t kRecordFileVersion = 1;
inline constexpr std::size_t kRecordColumnHeaderLen = 16;
inline constexpr std::uint32_t kRecordRowAlign = 64;
// Largest column accepted: bounds the memory of one insert (a block holds at most batch_size rows).
inline constexpr std::uint64_t kMaxRecordColumnBytes = std::uint64_t{1} << 30;

// A malformed column or record file (bad magic, version, header, length, CRC).
class RecordColumnError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

// No stored column for a block that a retrieval scope needs, or the columns of a scope differ in
// row size.
class RecordColumnMissing : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

struct RecordColumnInfo {
  std::uint32_t row_size = 0;
  std::uint32_t n_rows = 0;
  std::uint16_t version = kRecordColumnVersion;
};

// Checks magic, version, reserved == 0, row size (positive multiple of 64) and that the length is
// exactly header + n_rows * row_size. Throws RecordColumnError. Never reads outside [data, +size).
RecordColumnInfo parse_record_column(const std::uint8_t* data, std::size_t size);
inline RecordColumnInfo parse_record_column(const std::vector<std::uint8_t>& v) {
  return parse_record_column(v.data(), v.size());
}

// The file bytes of a column (container header + column). Does not validate the column.
std::vector<std::uint8_t> encode_record_file(const std::vector<std::uint8_t>& column);
// Durable write (temp, fsync, rename, fsync dir). Crash point InsertAfterRecordFile or
// ReplaceAfterRecordFile is the caller's.
void write_record_file(const std::string& path, const std::vector<std::uint8_t>& column);
// Returns the column; throws RecordColumnError on bad magic, version, CRC or column, and
// std::runtime_error if the file cannot be read.
std::vector<std::uint8_t> read_record_file(const std::string& path);
// Same checks on bytes already in memory.
std::vector<std::uint8_t> decode_record_file(const std::vector<std::uint8_t>& file);

// "s<16 hex>.oseg" -> "s<16 hex>.orec".
std::string record_file_name(const std::string& segment_name);
// True for a file name the store generates for a record file.
bool is_valid_record_name(const std::string& n);

// Row size of a document class (1, 2); 0 for any other value.
std::uint32_t doc_class_row_size(std::uint32_t doc_class);

struct DocColumnInfo {
  std::uint32_t doc_class = 0;
  std::uint32_t row_size = 0;
  std::uint32_t n_rows = 0;
};
// Checks magic, version 1, a known class, that the row size is the class's and that the length is
// exactly header + n_rows * row_size. Throws RecordColumnError.
DocColumnInfo parse_doc_column(const std::uint8_t* data, std::size_t size);
inline DocColumnInfo parse_doc_column(const std::vector<std::uint8_t>& v) {
  return parse_doc_column(v.data(), v.size());
}

// The document columns of one block, by class (ascending). Whole ODCL columns.
using DocumentColumns = std::map<std::uint32_t, std::vector<std::uint8_t>>;

// The file bytes of the columns (container header + the columns, class ascending).
std::vector<std::uint8_t> encode_doc_file(const DocumentColumns& cols);
void write_doc_file(const std::string& path, const DocumentColumns& cols);
// Throws RecordColumnError on bad magic, version, CRC, a column, or classes that do not ascend.
DocumentColumns decode_doc_file(const std::vector<std::uint8_t>& file);
DocumentColumns read_doc_file(const std::string& path);
// "s<16 hex>.oseg" -> "s<16 hex>.odoc"; true for a name the store generates for one.
std::string doc_file_name(const std::string& segment_name);
bool is_valid_doc_name(const std::string& n);
// The (class, n_rows) of each column of a document file, reading only the headers (no CRC check).
// Throws RecordColumnError on a malformed file, std::runtime_error if it cannot be read.
std::vector<std::pair<std::uint32_t, std::uint32_t>> read_doc_file_summary(const std::string& path);

// One block's slice of a concatenated scope.
struct RecordSpanInfo {
  std::uint32_t cluster = 0;
  std::string segment;
  std::uint64_t first_row = 0;
  std::uint64_t n_rows = 0;
};

// The record columns of a scope, joined: one column in the layout above (header, then all rows),
// plus where each block's rows sit. Order: ascending cluster id, then segment name (creation
// order).
struct RecordScopeColumns {
  std::vector<std::uint8_t> column;  // empty scope: a valid column with 0 rows (row_size 64)
  std::uint32_t row_size = 0;
  std::uint64_t n_rows = 0;
  std::vector<RecordSpanInfo> spans;
};

// The document columns of one class over a scope, joined: the rows back to back (no header), plus
// where each block's rows sit. Same order as RecordScopeColumns; a block without a column of the
// class has a span with 0 rows.
struct DocumentScopeColumns {
  bool collection_has_class = false;  // some block of the collection has a row of the class
  std::uint32_t doc_class = 0;
  std::uint32_t row_size = 0;  // the class size
  std::uint64_t n_rows = 0;
  std::vector<std::uint8_t> rows;  // n_rows * row_size bytes
  std::vector<RecordSpanInfo> spans;
};

}  // namespace ombra::storage
