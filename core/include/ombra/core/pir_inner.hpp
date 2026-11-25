// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// GPU inner products of the private record retrieval answer (BFV).
//
// For one bucket with a grid of r1 x r2 plaintext cells, the answer needs, for every b < r2,
//   inner_b = sum_{a < r1} EvalMult(sel1_a, cell[a * r2 + b])
// (OpenFHE: BFV ciphertext x coefficient-packed plaintext, accumulated with EvalAdd). These r1 * r2
// products dominate the answer; this class computes them on the GPU. Expansion, the
// ciphertext x ciphertext products, relinearization and compression stay on OpenFHE, which takes
// the raw words back (see pir_inner_gpu.hpp for the OpenFHE glue).
//
// The computation, on raw RNS words of the Q towers (no OpenFHE or CUDA type in this header):
//   1. every cell, an int64 coefficient vector with values in [0, t), is reduced into every tower
//      (core/kernels/encode.cu, launch_reduce_ints) and forward NTT'ed (core/kernels/ntt.cu).
//      OpenFHE's EvalMult by a plaintext lifts the coefficient stored modulo t (a negative v is
//      stored as v + t) as is, not centered, so [0, t) is the lift to reproduce;
//   2. out_b[comp] = sum_a plain_{a,b} * sel_a[comp] pointwise in evaluation form, both components
//      (core/kernels/plain_search.cu: dims = r1, blocks = r2).
// Public material only: ciphertext words and plaintext coefficients; no secret key can enter.
// Public functions throw std::invalid_argument for bad input and CudaError for CUDA failures.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

#include "ombra/core/plain_search.hpp"  // EncodeTables

namespace ombra::core {

class PirInnerDevice {
 public:
  // `tables`: the Q towers of the selector ciphertexts (moduli odd, < 2^60) and their NTT tables.
  PirInnerDevice(const EncodeTables& tables, std::size_t r1, std::size_t r2);
  ~PirInnerDevice();
  PirInnerDevice(const PirInnerDevice&) = delete;
  PirInnerDevice& operator=(const PirInnerDevice&) = delete;

  std::size_t poly_words() const;      // num_towers * N
  std::size_t selector_words() const;  // r1 * 2 * poly_words(): [a][component][tower][coeff]
  std::size_t out_words() const;       // r2 * 2 * poly_words(): [b][component][tower][coeff]

  // One bucket. `selectors`: the r1 selector ciphertexts, selector_words() reduced words in
  // evaluation form. `cells`: r1 * r2 coefficient vectors [a * r2 + b][N] with values in [0, t),
  // t the plaintext modulus (the output of the layout's bytes_to_coeffs as it is, without
  // centered(): the answer's centered() followed by MakeCoefPackedPlaintext is the same value
  // modulo t). The values are lifted as integers, so a centered (negative) value gives a different
  // ciphertext than OpenFHE's, which decrypts the same but is not equal. `out` is resized to
  // out_words(); out_b is what OpenFHE's EvalMult + EvalAdd gives, word for word.
  void inner(const std::uint64_t* selectors, const std::int64_t* cells,
             std::vector<std::uint64_t>& out);

  // GPU time (CUDA events) of the last inner(): reduction, NTT and products, without the copies.
  double last_gpu_seconds() const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace ombra::core
