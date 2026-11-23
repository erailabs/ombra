// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Record column: the sealed re-rank rows of one block, one row per slot, for private record
// retrieval. The server never opens a row (AES-GCM under a key only the client has); it checks the
// header, stores the bytes with the block and concatenates the columns of a scope for one call.
//
// Column layout (little endian, version 1), the client's `parse_column` in client/ombra/pir.py:
//   0   4  magic "ORCL"
//   4   2  version (u16, 1)
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
#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace ombra::storage {

inline constexpr std::uint16_t kRecordColumnVersion = 1;
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

}  // namespace ombra::storage
