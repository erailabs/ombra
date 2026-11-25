// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// ombra_bench_pir_inner: time of the inner products of one retrieval bucket, GPU against OpenFHE.
//
// Usage: ombra_bench_pir_inner [n_rows m row_size warmup iters]      (defaults 768 16 3136 2 10)
//
// The bucket grid (r1 x r2) is what make_shape of the layout gives for n_rows rows of row_size
// bytes and m = the number of rows asked per call (the fullest bucket, with the preset's
// plaintext size). Selectors are fresh encryptions of random synthetic scalars under a key made in
// this process, cells are random synthetic coefficients; no key leaves the process and nothing
// secret is printed. Prints one JSON object with the shape and times:
//   gpu_inner_seconds: reduction, NTT and products on the device (CUDA events, copies excluded),
//   gpu_total_seconds: the same plus the host copies, as seen by the caller of inner(),
//   cpu_seconds:       OpenFHE EvalMult + EvalAdd of the same r1 * r2 products (single thread
//                      unless OpenMP is set by the environment).
// The answer's other steps (expansion, ct x ct products, relinearization, compression) are not
// timed here.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <random>
#include <string>
#include <vector>

#include "ombra/core/pir_inner.hpp"
#include "ombra/core/pir_inner_gpu.hpp"
#include "ombra/pir/layout.hpp"
#include "ombra/pir/preset.hpp"
#include "openfhe.h"
#include "pir_inner_context.hpp"

using namespace lbcrypto;
using Clock = std::chrono::steady_clock;

namespace {
double seconds_since(Clock::time_point t0) {
  return std::chrono::duration<double>(Clock::now() - t0).count();
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 1 && argc != 6) {
    std::fprintf(stderr, "usage: ombra_bench_pir_inner [n_rows m row_size warmup iters]\n");
    return 2;
  }
  try {
    const std::uint64_t n_rows = argc == 6 ? std::stoull(argv[1]) : 768;
    const std::uint32_t m = argc == 6 ? static_cast<std::uint32_t>(std::stoul(argv[2])) : 16;
    const std::size_t row_size = argc == 6 ? std::stoul(argv[3]) : 3136;
    const int warmup = argc == 6 ? std::stoi(argv[4]) : 2;
    const int iters = argc == 6 ? std::stoi(argv[5]) : 10;
    if (warmup < 0 || iters < 1) {
      std::fprintf(stderr, "bad arguments\n");
      return 2;
    }
    const ombra::pir::PirParams pp{ombra::pir::preset::kRingDim,
                                   ombra::pir::preset::kPlaintextModulus};
    const std::vector<std::uint8_t> seed(ombra::pir::kSeedLen, 7);
    const ombra::pir::PirShape shape = ombra::pir::make_shape(n_rows, m, row_size, seed, pp);
    const std::size_t r1 = shape.r1, r2 = shape.r2;

    auto cc = ombra::core::pir_test::build_preset_context();
    const std::size_t n = cc->GetRingDimension();
    const std::uint64_t t = cc->GetEncodingParams()->GetPlaintextModulus();
    const auto kp = cc->KeyGen();
    std::mt19937_64 rng(11);

    std::vector<Ciphertext<DCRTPoly>> sel;
    for (std::size_t a = 0; a < r1; ++a) {
      sel.push_back(cc->Encrypt(
          kp.publicKey, cc->MakeCoefPackedPlaintext({static_cast<std::int64_t>(rng() & 1)})));
    }
    std::vector<std::int64_t> coeffs(r1 * r2 * n);  // [0, t): what OpenFHE stores and lifts
    std::vector<Plaintext> cells;
    for (std::size_t p = 0; p < r1 * r2; ++p) {
      std::vector<std::int64_t> raw(n);
      for (auto& x : raw) x = static_cast<std::int64_t>(rng() % t);
      std::copy(raw.begin(), raw.end(), coeffs.begin() + p * n);
      cells.push_back(cc->MakeCoefPackedPlaintext(ombra::core::pir_test::centered(raw, t)));
    }

    const ombra::core::EncodeTables tables = ombra::core::extract_pir_inner_tables(cc, sel[0]);
    ombra::core::PirInnerDevice dev(tables, r1, r2);
    const auto words = ombra::core::flatten_pir_ciphertexts(sel, tables);
    std::vector<std::uint64_t> out;

    for (int i = 0; i < warmup; ++i) dev.inner(words.data(), coeffs.data(), out);
    double gpu_inner = 0, gpu_total = 0;
    for (int i = 0; i < iters; ++i) {
      const auto t0 = Clock::now();
      dev.inner(words.data(), coeffs.data(), out);
      gpu_total += seconds_since(t0);
      gpu_inner += dev.last_gpu_seconds();
    }

    double cpu = 0;
    const int cpu_iters = std::max(1, iters / 5);
    for (int i = 0; i < cpu_iters; ++i) {
      const auto t0 = Clock::now();
      for (std::size_t b = 0; b < r2; ++b) {
        Ciphertext<DCRTPoly> inner;
        for (std::size_t a = 0; a < r1; ++a) {
          const auto term = cc->EvalMult(sel[a], cells[a * r2 + b]);
          inner = inner ? cc->EvalAdd(inner, term) : term;
        }
      }
      cpu += seconds_since(t0);
    }

    std::printf(
        "{\"n_rows\": %llu, \"m\": %u, \"row_size\": %zu, \"r1\": %zu, \"r2\": %zu, "
        "\"towers\": %zu, \"ring_dim\": %zu, \"products\": %zu, \"iters\": %d, "
        "\"gpu_inner_seconds\": %.6f, \"gpu_total_seconds\": %.6f, \"cpu_seconds\": %.6f}\n",
        static_cast<unsigned long long>(n_rows), m, row_size, r1, r2, tables.num_towers, n, r1 * r2,
        iters, gpu_inner / iters, gpu_total / iters, cpu / cpu_iters);
    return 0;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 2;
  }
}
