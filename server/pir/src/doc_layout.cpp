// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include <algorithm>
#include <cstring>

#include "ombra/pir/documents.hpp"

namespace ombra::pir {

PlaneLayout plane_layout(std::size_t class_size, const PirParams& pp) {
  if (class_size == 0 || class_size % 64 != 0)
    throw PirError("class size must be a positive multiple of 64");
  const std::size_t p = pp.plaintext_bytes();
  return {(class_size + p - 1) / p, std::min(class_size, p)};
}

DocShape make_doc_shape(std::uint64_t n_rows, std::uint32_t m, std::size_t class_size,
                        const std::vector<std::uint8_t>& seed, const PirParams& pp) {
  const auto lay = plane_layout(class_size, pp);
  DocShape d;
  d.shape = make_shape(std::max<std::uint64_t>(1, n_rows), m, lay.plane_row, seed, pp);
  d.class_size = class_size;
  d.planes = lay.planes;
  return d;
}

std::vector<std::uint8_t> plane_body(const std::uint8_t* rows, std::uint64_t n_rows,
                                     std::size_t class_size, std::size_t plane,
                                     const PirParams& pp) {
  const auto lay = plane_layout(class_size, pp);
  if (plane >= lay.planes) throw PirError("plane out of range");
  std::vector<std::uint8_t> out(static_cast<std::size_t>(n_rows) * lay.plane_row, 0);
  if (!rows) return out;
  const std::size_t from = plane * lay.plane_row;
  const std::size_t take = std::min(lay.plane_row, class_size - from);
  for (std::uint64_t r = 0; r < n_rows; ++r)
    std::memcpy(out.data() + r * lay.plane_row, rows + r * class_size + from, take);
  return out;
}

}  // namespace ombra::pir
