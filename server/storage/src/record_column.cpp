// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/storage/record_column.hpp"

#include <algorithm>
#include <cstring>
#include <fstream>

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
  if (version < kRecordColumnMinVersion || version > kRecordColumnVersion)
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
  return {row_size, n_rows, static_cast<std::uint16_t>(version)};
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

std::uint32_t doc_class_row_size(std::uint32_t c) {
  return c == kDocClass16K ? kDocClass16KBytes : c == kDocClass256K ? kDocClass256KBytes : 0;
}

DocColumnInfo parse_doc_column(const std::uint8_t* data, std::size_t size) {
  if (size < kRecordColumnHeaderLen) throw RecordColumnError("document column: truncated header");
  if (std::memcmp(data, "ODCL", 4) != 0) throw RecordColumnError("document column: bad magic");
  const auto version = get_le(data + 4, 2);
  if (version != kDocColumnVersion)
    throw RecordColumnError("document column: unsupported version " + std::to_string(version));
  const auto cls = static_cast<std::uint32_t>(get_le(data + 6, 2));
  const auto row_size = static_cast<std::uint32_t>(get_le(data + 8, 4));
  const auto n_rows = static_cast<std::uint32_t>(get_le(data + 12, 4));
  if (doc_class_row_size(cls) == 0 || doc_class_row_size(cls) != row_size)
    throw RecordColumnError("document column: malformed header");
  if (static_cast<std::uint64_t>(n_rows) * row_size != size - kRecordColumnHeaderLen)
    throw RecordColumnError("document column: length does not match its header");
  return {cls, row_size, n_rows};
}

std::vector<std::uint8_t> encode_doc_file(const DocumentColumns& cols) {
  std::vector<std::uint8_t> body;
  for (const auto& kv : cols) body.insert(body.end(), kv.second.begin(), kv.second.end());
  std::vector<std::uint8_t> out{'O', 'D', 'O', 'C'};
  put_le(out, kDocFileVersion, 2);
  put_le(out, crc32(body.data(), body.size()), 4);
  out.insert(out.end(), body.begin(), body.end());
  return out;
}

void write_doc_file(const std::string& path, const DocumentColumns& cols) {
  write_file_durable(path, encode_doc_file(cols));
}

namespace {
// The column at b[off..): its header is checked and its total length returned.
std::size_t doc_column_len(const std::uint8_t* p, std::size_t avail, DocColumnInfo* info) {
  if (avail < kRecordColumnHeaderLen) throw RecordColumnError("document file: truncated column");
  if (std::memcmp(p, "ODCL", 4) != 0) throw RecordColumnError("document file: bad column magic");
  const auto cls = static_cast<std::uint32_t>(get_le(p + 6, 2));
  const auto rs = doc_class_row_size(cls);
  const auto n = static_cast<std::uint32_t>(get_le(p + 12, 4));
  if (rs == 0) throw RecordColumnError("document file: unknown class");
  const std::uint64_t len = kRecordColumnHeaderLen + static_cast<std::uint64_t>(n) * rs;
  if (len > avail) throw RecordColumnError("document file: truncated column");
  if (info) *info = {cls, rs, n};
  return static_cast<std::size_t>(len);
}
}  // namespace

DocumentColumns decode_doc_file(const std::vector<std::uint8_t>& b) {
  if (b.size() < kFileHeader) throw RecordColumnError("document file: truncated header");
  if (std::memcmp(b.data(), "ODOC", 4) != 0) throw RecordColumnError("document file: bad magic");
  const auto version = get_le(b.data() + 4, 2);
  if (version != kDocFileVersion)
    throw RecordColumnError("document file: unknown container version " + std::to_string(version));
  const auto crc = static_cast<std::uint32_t>(get_le(b.data() + 6, 4));
  const std::uint8_t* p = b.data() + kFileHeader;
  std::size_t left = b.size() - kFileHeader;
  if (crc32(p, left) != crc) throw RecordColumnError("document file: CRC mismatch");
  DocumentColumns out;
  std::uint32_t last = 0;
  while (left > 0) {
    DocColumnInfo info;
    const std::size_t len = doc_column_len(p, left, &info);
    if (info.doc_class <= last) throw RecordColumnError("document file: classes do not ascend");
    parse_doc_column(p, len);
    out[info.doc_class].assign(p, p + len);
    last = info.doc_class;
    p += len;
    left -= len;
  }
  return out;
}

DocumentColumns read_doc_file(const std::string& path) { return decode_doc_file(read_file(path)); }

std::vector<std::pair<std::uint32_t, std::uint32_t>> read_doc_file_summary(
    const std::string& path) {
  std::ifstream f(path, std::ios::binary);
  if (!f) throw std::runtime_error("cannot read " + path);
  f.seekg(0, std::ios::end);
  const std::uint64_t size = static_cast<std::uint64_t>(f.tellg());
  std::uint8_t head[kFileHeader];
  f.seekg(0);
  if (size < kFileHeader || !f.read(reinterpret_cast<char*>(head), kFileHeader))
    throw RecordColumnError("document file: truncated header");
  if (std::memcmp(head, "ODOC", 4) != 0 || get_le(head + 4, 2) != kDocFileVersion)
    throw RecordColumnError("document file: bad header");
  std::vector<std::pair<std::uint32_t, std::uint32_t>> out;
  std::uint64_t off = kFileHeader;
  while (off < size) {
    std::uint8_t h[kRecordColumnHeaderLen];
    f.seekg(static_cast<std::streamoff>(off));
    if (size - off < kRecordColumnHeaderLen || !f.read(reinterpret_cast<char*>(h), sizeof h))
      throw RecordColumnError("document file: truncated column");
    DocColumnInfo info;
    off += doc_column_len(h, static_cast<std::size_t>(size - off), &info);
    out.emplace_back(info.doc_class, info.n_rows);
  }
  return out;
}

std::string doc_file_name(const std::string& segment_name) {
  if (!is_valid_segment_name(segment_name)) throw std::runtime_error("invalid segment name");
  return segment_name.substr(0, 17) + ".odoc";
}

bool is_valid_doc_name(const std::string& n) {
  if (n.size() != 22 || n.compare(17, 5, ".odoc") != 0) return false;
  return is_valid_segment_name(n.substr(0, 17) + ".oseg");
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
