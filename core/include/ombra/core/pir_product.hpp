// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// GPU second-dimension product of the private record retrieval answer (BFV).
//
// For one bucket the answer needs, over the r2 pairs (x_b, y_b) of a second-dimension selector x_b
// and an inner sum y_b (all 2-component ciphertexts over Q, evaluation form),
//   total = sum_b EvalMultNoRelin(x_b, y_b)
// (OpenFHE BFV, HPSPOVERQLEVELED at the top level; see core/kernels/bfv_product.cu for the steps).
// This class computes `total` (3 components over Q, coefficient form, as OpenFHE leaves it) on the
// GPU, word for word. Relinearization, compression and serialization stay on OpenFHE: they are
// about 0.1% of a call. No OpenFHE or CUDA type here; the OpenFHE glue is pir_inner_gpu.hpp.
// Public material only: ciphertext words and public parameters of the context.
// Public functions throw std::invalid_argument for bad input and CudaError for CUDA failures.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace ombra::core {

// Everything the kernels need, extracted from the crypto parameters of the context. Tower order is
// Q towers then R towers (R = OpenFHE's P_l, the extension basis of the product).
struct PirProductTables {
  std::size_t ring_dim = 0, nq = 0, nr = 0;
  std::vector<std::uint64_t> moduli;       // [nq + nr]
  std::vector<std::uint64_t> psi_rev;      // [nq + nr][N], forward NTT twiddles (bit-reversed)
  std::vector<std::uint64_t> psi_inv_rev;  // [nq + nr][N]
  std::vector<std::uint64_t> n_inv;        // [nq + nr]
  // Basis conversion constants, layouts as in BfvProdTables (core/kernels/kernel_launch.hpp).
  std::vector<std::uint64_t> ex_hat_inv, ex_hat_mod, ex_alpha;
  std::vector<double> q_inv;
  std::vector<std::uint64_t> b_hat_inv, b_conv, r_hat_inv, r_hat_mod, r_alpha;
  std::vector<double> r_inv;
  std::vector<std::uint64_t> sr_tab;
  std::vector<double> sr_frac;
};

class PirProductDevice {
 public:
  PirProductDevice(const PirProductTables& tables, std::size_t pairs);
  ~PirProductDevice();
  PirProductDevice(const PirProductDevice&) = delete;
  PirProductDevice& operator=(const PirProductDevice&) = delete;

  std::size_t pairs() const;
  std::size_t ct_words() const;   // 2 * nq * N: one ciphertext over Q
  std::size_t out_words() const;  // 3 * nq * N: the total

  // `x`, `y`: pairs() ciphertexts each, [pair][component][tower][coeff], reduced words, evaluation
  // form. `out` is resized to out_words(): [component 0..2][tower][coeff], coefficient form.
  void product_sum(const std::uint64_t* x, const std::uint64_t* y, std::vector<std::uint64_t>& out);

  double last_gpu_seconds() const;  // CUDA events, without the copies

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace ombra::core
