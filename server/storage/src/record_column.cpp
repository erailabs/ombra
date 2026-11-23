// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/storage/record_column.hpp"

#include <cstring>

#include "ombra/storage/fsutil.hpp"
#include "ombra/storage/segment.hpp"

namespace ombra::storage {

namespace {
constexpr std::size_t kFileHeader = 10;
}  // namespace

RecordColumnInfo parse_record_column(const std::uint8_t* data, std::size_t size) {
  if (size < kRecordColumnHeaderLen) throw RecordColumnError("record column: truncated header");
  if (std::memcmp(data, "ORCL", 4) != 0) throw RecordColumnError("record column: bad magic");
  const auto version = get_le(data + 4, 2);
  if (version != kRecordColumnVersion)
    throw RecordColumnError("record column: unsupported version " + std::to_string(version));
  const auto reserved = get_le(data + 6, 2);
  const auto row_size = static_cast<std::uint32_t>(get_le(data + 8, 4));
  const auto n_rows = static_cast<std::uint32_t>(get_le(data + 12, 4));
  if (reserved != 0 || row_size == 0 || row_size % kRecordRowAlign != 0)
    throw RecordColumnError("record column: malformed header");
  // u32 x u32 cannot overflow u64.
  const std::uint64_t body = static_cast<std::uint64_t>(n_rows) * row_size;
  if (body != size - kRecordColumnHeaderLen)
    throw RecordColumnError("record column: length does not match its header");
  return {row_size, n_rows};
}

std::vector<std::uint8_t> encode_record_file(const std::vector<std::uint8_t>& column) {
  std::vector<std::uint8_t> out{'O', 'R', 'E', 'C'};
  put_le(out, kRecordFileVersion, 2);
  put_le(out, crc32(column.data(), column.size()), 4);
  out.insert(out.end(), column.begin(), column.end());
  return out;
}

void write_record_file(const std::string& path, const std::vector<std::uint8_t>& column) {
  write_file_durable(path, encode_record_file(column));
}

std::vector<std::uint8_t> decode_record_file(const std::vector<std::uint8_t>& b) {
  if (b.size() < kFileHeader) throw RecordColumnError("record file: truncated header");
  if (std::memcmp(b.data(), "OREC", 4) != 0) throw RecordColumnError("record file: bad magic");
  const auto version = get_le(b.data() + 4, 2);
  if (version != kRecordFileVersion)
    throw RecordColumnError("record file: unknown container version " + std::to_string(version));
  const auto crc = static_cast<std::uint32_t>(get_le(b.data() + 6, 4));
  const std::uint8_t* p = b.data() + kFileHeader;
  const std::size_t n = b.size() - kFileHeader;
  if (crc32(p, n) != crc) throw RecordColumnError("record file: CRC mismatch");
  parse_record_column(p, n);
  return std::vector<std::uint8_t>(p, p + n);
}

std::vector<std::uint8_t> read_record_file(const std::string& path) {
  return decode_record_file(read_file(path));
}

std::string record_file_name(const std::string& segment_name) {
  if (!is_valid_segment_name(segment_name)) throw std::runtime_error("invalid segment name");
  return segment_name.substr(0, 17) + ".orec";
}

bool is_valid_record_name(const std::string& n) {
  if (n.size() != 22 || n.compare(17, 5, ".orec") != 0) return false;
  return is_valid_segment_name(n.substr(0, 17) + ".oseg");
}

}  // namespace ombra::storage
