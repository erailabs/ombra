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

// Where the time of one answer call went, in milliseconds (steady clock, summed over the buckets of
// the call), and the sizes of the call. Counts and durations only: nothing of any ciphertext,
// plaintext, row or id. Stage meanings (the CPU path; with a GPU hook the first-dimension stage is
// the hook, including the packing of the cells for it):
//   setup       reading the context and loading the keys (answer_call only)
//   read        reading and checking the query ciphertexts
//   expand      query expansion
//   first_dim   cell encoding and the first-dimension inner sums
//   second_dim  the second-dimension products and their sum
//   compress    relinearization and modulus drop of the bucket answer
//   serialize   serializing the answer
// total_ms is the whole call, so the stages add up to at most total_ms.
struct PirStageTimes {
  double setup_ms = 0, read_ms = 0, expand_ms = 0, first_dim_ms = 0, second_dim_ms = 0,
         compress_ms = 0, serialize_ms = 0, total_ms = 0;
  std::uint64_t rows = 0, buckets = 0, n_cts = 0, r1 = 0, r2 = 0;
  double stages_ms() const {
    return setup_ms + read_ms + expand_ms + first_dim_ms + second_dim_ms + compress_ms +
           serialize_ms;
  }
};

// Reads the context, checks it against the preset, loads the keys and unloads them again. Throws
// PirContextError, PirKeyTagMismatch.
void check_retrieval_keys(const PirKeyFiles& files, const std::string& key_tag,
                          const PirShape& shape);

// Where the first-dimension inner sums run. kGpu uses the GPU backend if the build registered one
// (see answer.hpp) and OpenFHE on the CPU otherwise; the answers are byte-identical either way.
enum class PirBackend { kCpu, kGpu };

// CUDA build only (library ombra_pir_gpu, gpu_inner.hpp): makes PirBackend::kGpu use the GPU
// inner sums. Call once at start-up, only on a machine with a usable GPU.
void register_gpu_inner_sums();

// Loads the keys, answers every query, unloads. `column_body` points at n_rows * row_size bytes.
// Returns one serialized answer per bucket, in bucket order. Throws PirContextError,
// PirKeyTagMismatch, PirQueryError, PirError. If `times` is not null it receives the stage times.
std::vector<std::string> answer_call(const PirKeyFiles& files, const std::string& key_tag,
                                     const PirShape& shape, const std::uint8_t* column_body,
                                     const std::vector<std::string>& queries,
                                     PirBackend backend = PirBackend::kCpu,
                                     PirStageTimes* times = nullptr);

}  // namespace ombra::pir
