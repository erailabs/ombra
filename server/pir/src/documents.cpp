// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/pir/documents.hpp"

namespace ombra::pir {

std::vector<std::string> answer_documents_call(const PirKeyFiles& files, const std::string& key_tag,
                                               const DocShape& doc, const std::uint8_t* rows,
                                               const std::vector<std::string>& queries,
                                               PirBackend backend) {
  std::vector<std::string> out;
  out.reserve(doc.n_answers());
  for (std::size_t p = 0; p < doc.planes; ++p) {
    // One plane at a time: a plane of a 256 KiB database is 14 times smaller than the whole.
    const auto body = plane_body(rows, doc.shape.n_rows, doc.class_size, p, doc.shape.pp);
    auto part = answer_call(files, key_tag, doc.shape, body.data(), queries, backend);
    for (auto& a : part) out.push_back(std::move(a));
  }
  return out;
}

}  // namespace ombra::pir
