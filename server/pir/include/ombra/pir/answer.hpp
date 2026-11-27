// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The server side of private record retrieval on OpenFHE (CPU): the check of the uploaded BFV
// context, the loading of the public evaluation keys, and the answer. The math is the one of
// client/ombra/pir_reference.py (PirServerReference), step for step; the answers must decrypt to
// the same bytes. Only public material goes in: the serialized context and evaluation keys, the
// record column and the serialized query ciphertexts. There is no key generation and no decryption
// here, and no secret key anywhere.
//
// Expansion: L levels of x -> x^(N/2^j + 1). The odd branch is EvalSub, EvalMult by the +x^(N-2^j)
// monomial, then EvalNegate. Answer per bucket: sum_b EvalMultNoRelin(sel2_b, sum_a
// EvalMult(sel1_a, pt[a, b])), then Relinearize, then Compress to one tower, serialized BINARY.
#pragma once

#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

#include "ombra/pir/errors.hpp"
#include "ombra/pir/layout.hpp"
#include "ombra/pir/retrieval.hpp"
#include "openfhe.h"

namespace ombra::pir {

using PirContext = lbcrypto::CryptoContext<lbcrypto::DCRTPoly>;

// The layout numbers of the retrieval preset (ring dimension, plaintext modulus).
PirParams preset_params();

// Reads a serialized crypto context (the payload of a context envelope) and requires it to be the
// retrieval preset: scheme BFV, ring dimension 8192, plaintext modulus 786433, the same moduli,
// key switch technique and digit size as the context built from the preset, the security level of
// the preset (at least HEStd_128_classic) and the 128-bit floor of core/. Throws PirContextError.
PirContext read_pir_context(const std::vector<std::uint8_t>& payload);

// Loads the relinearization and automorphism keys into OpenFHE's process-wide key stores under
// the key tag they carry, and removes exactly those entries again when destroyed. Entries of
// other tags (the search keys of the collections) are neither read nor cleared. Throws
// PirKeyTagMismatch if the files carry a tag other than `expected_tag`, if the tag is already
// present in the stores (another key set holds it), or if a needed key is missing.
//
// The stores are global: the caller holds a mutex for the whole life of this object (and the one
// that serializes every other writer of the stores while it loads and unloads).
class PirKeys {
 public:
  PirKeys(const PirContext& cc, const std::vector<std::uint8_t>& mult_payload,
          const std::vector<std::uint8_t>& automorphism_payload, const std::string& expected_tag,
          const PirShape& shape);
  ~PirKeys();
  PirKeys(const PirKeys&) = delete;
  PirKeys& operator=(const PirKeys&) = delete;

  const std::string& tag() const { return tag_; }

 private:
  std::string tag_;
};

// Optional replacement of the first-dimension inner sums of one bucket (the CPU computes them with
// OpenFHE; a GPU backend can compute the same ciphertexts, word for word). Receives the r1
// selector ciphertexts (the expansion outputs base .. base + r1 - 1, evaluation form) and the
// r1 * r2 cells, flat: cell [a * r2 + b] is the ring_dim int64 coefficients at offset
// (a * r2 + b) * ring_dim, values in [0, plain_modulus) exactly as bytes_to_coeffs returns them
// (NOT centered; a missing cell is all zero). Returns r2 ciphertexts, inner_b = sum_a
// EvalMult(sel_a, cell[a * r2 + b]) as OpenFHE computes it. It may throw; the call then fails.
// One instance is made per call and used for its buckets in order, so it may keep state.
using InnerSums = std::function<std::vector<lbcrypto::Ciphertext<lbcrypto::DCRTPoly>>(
    const PirContext& cc, const std::vector<lbcrypto::Ciphertext<lbcrypto::DCRTPoly>>& selectors,
    const std::vector<std::int64_t>& cells, std::size_t r1, std::size_t r2)>;

// Called once per bucket, in bucket order, with the serialized compressed answer.
using AnswerSink = std::function<void(std::size_t bucket, std::string&& answer)>;

// Answers one call. `column_body` points at the rows (n_rows * row_size bytes, behind the column
// header). `queries` are the serialized query ciphertexts, shape.n_cts of them. Throws
// PirQueryError for a bad query and PirError for a wrong count. `keys` must be alive.
void answer_queries(const PirContext& cc, const PirKeys& keys, const PirShape& shape,
                    const std::uint8_t* column_body, const std::vector<std::string>& queries,
                    const AnswerSink& sink, const InnerSums& inner_sums = nullptr,
                    PirStageTimes* times = nullptr);

// Same as answer_call (retrieval.hpp) with an explicit inner-sums hook (empty: OpenFHE).
std::vector<std::string> answer_call_with_hook(const PirKeyFiles& files, const std::string& key_tag,
                                               const PirShape& shape,
                                               const std::uint8_t* column_body,
                                               const std::vector<std::string>& queries,
                                               const InnerSums& inner_sums,
                                               PirStageTimes* times = nullptr);

// A GPU backend registers a factory here at start-up (the CUDA build of the server does); the
// factory makes one InnerSums per call. answer_call with PirBackend::kGpu uses it, and falls back
// to OpenFHE on the CPU if none is registered.
using InnerSumsFactory = std::function<InnerSums()>;
void set_gpu_inner_sums_factory(InnerSumsFactory factory);

}  // namespace ombra::pir
