// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The entry points the gRPC service uses, with no OpenFHE type in the header (OpenFHE's headers
// need its own compile definitions, which only the answer library is built with).
//
// OpenFHE keeps evaluation keys in process-wide stores by key tag. Both functions put the
// retrieval keys in under their own tag and take exactly that tag out again before they return,
// never touching other tags (the search keys of the collections). The CALLER must hold the mutex
// that serializes every writer and reader of those stores for the whole call.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "ombra/pir/errors.hpp"
#include "ombra/pir/layout.hpp"

namespace ombra::pir {

// The payloads (envelope contents) of the three stored retrieval key files.
struct PirKeyFiles {
  std::vector<std::uint8_t> context;
  std::vector<std::uint8_t> eval_mult;
  std::vector<std::uint8_t> automorphism;
};

// The layout numbers of the retrieval preset.
PirParams retrieval_params();

// Reads the context, checks it against the preset, loads the keys and unloads them again. Throws
// PirContextError, PirKeyTagMismatch.
void check_retrieval_keys(const PirKeyFiles& files, const std::string& key_tag,
                          const PirShape& shape);

// Loads the keys, answers every query, unloads. `column_body` points at n_rows * row_size bytes.
// Returns one serialized answer per bucket, in bucket order. Throws PirContextError,
// PirKeyTagMismatch, PirQueryError, PirError.
std::vector<std::string> answer_call(const PirKeyFiles& files, const std::string& key_tag,
                                     const PirShape& shape, const std::uint8_t* column_body,
                                     const std::vector<std::string>& queries);

}  // namespace ombra::pir
