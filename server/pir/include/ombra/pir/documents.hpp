// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The server side of a private document call (RetrieveDocuments): the planes of a document class
// and their answer. A document of S bytes is cut into c = ceil(S / P) chunks of P = one plaintext
// of the preset (19,456 bytes), the last one zero padded; for S <= P the single chunk is the
// document. Plane p is the PIR database of chunk p of every row. All planes share one shape (rows
// of min(S, P) bytes), one seed and one cuckoo layout, so one set of queries serves every plane.
// The answers are plane-major: the answers of plane 0 in bucket order, then plane 1, ... The
// client's `plane_layout`, `make_doc_shape` and `plane_chunk` (client/ombra/pir.py) are the model.
// No OpenFHE type here; the per-plane answer is `answer_call` (retrieval.hpp).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "ombra/pir/layout.hpp"
#include "ombra/pir/retrieval.hpp"

namespace ombra::pir {

// (planes, plane row size) of a document class of `class_size` bytes. Throws PirError unless the
// size is a positive multiple of 64.
struct PlaneLayout {
  std::size_t planes = 0;
  std::size_t plane_row = 0;
};
PlaneLayout plane_layout(std::size_t class_size, const PirParams& pp);

// One shape shared by `planes` planes. n_rows is the database size (>= 1: an empty database is
// answered over one padding row of zeros, which the caller passes as n_rows 1).
struct DocShape {
  PirShape shape;
  std::size_t class_size = 0;
  std::size_t planes = 0;
  std::size_t n_answers() const { return planes * shape.buckets; }
};
DocShape make_doc_shape(std::uint64_t n_rows, std::uint32_t m, std::size_t class_size,
                        const std::vector<std::uint8_t>& seed, const PirParams& pp);

// Chunk `plane` of each of `n_rows` rows (class_size bytes each, back to back at `rows`), zero
// padded to the plane row size: n_rows * plane_row bytes. rows == nullptr: n_rows rows of zeros.
std::vector<std::uint8_t> plane_body(const std::uint8_t* rows, std::uint64_t n_rows,
                                     std::size_t class_size, std::size_t plane,
                                     const PirParams& pp);

// Answers one document call: for each plane in turn, builds its body and answers every query with
// `answer_call`. `rows` holds doc.shape.n_rows rows of doc.class_size bytes, or is nullptr for the
// padding row. Returns planes * buckets answers, plane-major. Same locking rule and exceptions as
// answer_call.
std::vector<std::string> answer_documents_call(const PirKeyFiles& files, const std::string& key_tag,
                                               const DocShape& doc, const std::uint8_t* rows,
                                               const std::vector<std::string>& queries,
                                               PirBackend backend = PirBackend::kCpu);

}  // namespace ombra::pir
