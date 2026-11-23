// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Fuzz target: the record column parser and the record file container
// (server/storage/src/record_column.cpp). First byte: mode. Mode bit 0 set: the rest is wrapped in a
// valid record file container (magic, version, CRC), so the fuzzer reaches the column check behind
// the CRC. Otherwise the rest is parsed as a bare column and, separately, as a file as it is.
// A column that parses must have exactly header + n_rows * row_size bytes and survive the container
// round trip unchanged.
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <vector>

#include "ombra/storage/record_column.hpp"

namespace st = ombra::storage;

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* d, std::size_t n) {
  if (n < 1) return 0;
  const std::vector<std::uint8_t> body(d + 1, d + n);
  if ((d[0] & 1) != 0) {
    try {
      const auto col = st::decode_record_file(st::encode_record_file(body));
      if (col != body) std::abort();
    } catch (const st::RecordColumnError&) {
    }
    return 0;
  }
  try {
    const auto info = st::parse_record_column(body);
    if (body.size() != st::kRecordColumnHeaderLen +
                           static_cast<std::uint64_t>(info.n_rows) * info.row_size ||
        info.row_size == 0 || info.row_size % st::kRecordRowAlign != 0)
      std::abort();
    if (st::decode_record_file(st::encode_record_file(body)) != body) std::abort();
  } catch (const st::RecordColumnError&) {
  }
  try {
    (void)st::decode_record_file(body);
  } catch (const st::RecordColumnError&) {
  }
  return 0;
}
