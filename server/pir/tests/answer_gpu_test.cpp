// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The retrieval answer with the GPU inner sums against the answer on OpenFHE: for several shapes
// (a bucket with missing cells, r1 and r2 above 1, more than one query ciphertext) the serialized
// answers of answer_call must be byte-identical with and without the GPU hook. Keys and queries
// are made here (the client's role); the library under test never sees a secret key. Synthetic
// data only; prints PASS or FAIL per shape, never rows, queries or keys. Needs a GPU.
#include <chrono>
#include <cstdio>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "ombra/pir/answer.hpp"
#include "ombra/pir/gpu_inner.hpp"
#include "ombra/pir/preset.hpp"
#include "ombra/pir/retrieval.hpp"
#include "openfhe.h"
#include "scheme/bfvrns/bfvrns-ser.h"

using namespace lbcrypto;
using namespace ombra::pir;
using Bytes = std::vector<std::uint8_t>;
using Impl = CryptoContextImpl<DCRTPoly>;

namespace {

bool g_ok = true;

template <class T>
Bytes ser(const T& x) {
  std::ostringstream out(std::ios::out | std::ios::binary);
  Serial::Serialize(x, out, SerType::BINARY);
  const std::string s = out.str();
  return Bytes(s.begin(), s.end());
}

CryptoContext<DCRTPoly> context() {
  CCParams<CryptoContextBFVRNS> p;
  p.SetSecurityLevel(HEStd_128_classic);
  p.SetRingDim(preset::kRingDim);
  p.SetPlaintextModulus(preset::kPlaintextModulus);
  p.SetMultiplicativeDepth(preset::kMultiplicativeDepth);
  p.SetKeySwitchTechnique(BV);
  p.SetDigitSize(preset::kDigitSize);
  auto cc = GenCryptoContext(p);
  cc->Enable(PKE);
  cc->Enable(KEYSWITCH);
  cc->Enable(LEVELEDSHE);
  return cc;
}

struct Case {
  const char* name;
  std::uint64_t n_rows;
  std::uint32_t m;
  std::size_t row_size;
  std::size_t want_r1, want_r2;  // 0: not checked
  std::size_t want_cts;          // 0: not checked
  bool want_missing;             // some bucket has fewer cells than r1 * r2
};

void run(const Case& c, std::uint64_t seed) {
  const PirParams pp = preset_params();
  const PirShape shape = make_shape(c.n_rows, c.m, c.row_size, Bytes(16, 5), pp);
  bool missing = false;
  for (const auto& rows : shape.contents)
    missing =
        missing || (rows.size() + shape.rows_per_pt - 1) / shape.rows_per_pt < shape.r1 * shape.r2;
  const bool shape_ok = (!c.want_r1 || shape.r1 == c.want_r1) &&
                        (!c.want_r2 || shape.r2 == c.want_r2) &&
                        (!c.want_cts || shape.n_cts == c.want_cts) && (!c.want_missing || missing);
  std::printf("%s shape r1=%zu r2=%zu cts=%zu buckets=%zu missing=%d: %s\n", c.name, shape.r1,
              shape.r2, shape.n_cts, shape.buckets, missing ? 1 : 0, shape_ok ? "PASS" : "FAIL");
  g_ok = g_ok && shape_ok;

  std::mt19937_64 rng(seed);
  Bytes body(c.n_rows * c.row_size);
  for (auto& b : body) b = static_cast<std::uint8_t>(rng());

  const auto cc = context();
  const auto kp = cc->KeyGen();  // secret key: this process only
  cc->EvalMultKeyGen(kp.secretKey);
  cc->EvalAutomorphismKeyGen(kp.secretKey, shape.automorphism_indices());
  const std::string tag = kp.publicKey->GetKeyTag();
  std::ostringstream mo(std::ios::out | std::ios::binary), ao(std::ios::out | std::ios::binary);
  Impl::SerializeEvalMultKey(mo, SerType::BINARY, tag);
  Impl::SerializeEvalAutomorphismKey(ao, SerType::BINARY, tag);
  PirKeyFiles files;
  files.context = ser(cc);
  const std::string ms = mo.str(), as = ao.str();
  files.eval_mult.assign(ms.begin(), ms.end());
  files.automorphism.assign(as.begin(), as.end());
  Impl::ClearEvalMultKeys(tag);
  Impl::ClearEvalAutomorphismKeys(tag);

  // Queries: one selected cell per bucket, as the client makes them (scaled by 2^-L mod t).
  const std::int64_t t = static_cast<std::int64_t>(pp.plain_modulus);
  std::int64_t inv = 1;
  for (std::size_t i = 0; i < shape.levels; ++i) inv = inv * ((t + 1) / 2) % t;
  if (inv > t / 2) inv -= t;
  std::vector<std::string> queries;
  for (std::size_t k = 0; k < shape.n_cts; ++k) {
    std::vector<std::int64_t> q(pp.ring_dim, 0);
    for (std::size_t slot = 0; slot < shape.buckets_in_ct(k); ++slot) {
      const std::size_t base = slot * shape.coeffs_per_bucket();
      q[base + rng() % shape.r1] = inv;
      q[base + shape.r1 + rng() % shape.r2] = inv;
    }
    const Bytes b = ser(cc->Encrypt(kp.publicKey, cc->MakeCoefPackedPlaintext(q)));
    queries.emplace_back(b.begin(), b.end());
  }

  using Clock = std::chrono::steady_clock;
  const auto t0 = Clock::now();
  const auto cpu = answer_call(files, tag, shape, body.data(), queries, PirBackend::kCpu);
  const auto t1 = Clock::now();
  const auto gpu =
      answer_call_with_hook(files, tag, shape, body.data(), queries, make_gpu_inner_sums());
  const auto t2 = Clock::now();
  std::size_t diff = 0;
  for (std::size_t b = 0; b < shape.buckets; ++b) diff += cpu.at(b) != gpu.at(b);
  const bool same = diff == 0 && cpu.size() == gpu.size();
  std::printf(
      "%s serialized answers byte-identical (%zu/%zu buckets) cpu %.1f s gpu-hook %.1f s: %s\n",
      c.name, shape.buckets - diff, shape.buckets, std::chrono::duration<double>(t1 - t0).count(),
      std::chrono::duration<double>(t2 - t1).count(), same ? "PASS" : "FAIL");
  g_ok = g_ok && same;

  // Through the registered backend, as the server calls it (the small cases only: time).
  if (shape.buckets > 100) return;
  register_gpu_inner_sums();
  const auto reg = answer_call(files, tag, shape, body.data(), queries, PirBackend::kGpu);
  const bool reg_same = reg == cpu;
  std::printf("%s PirBackend::kGpu equals the CPU answers: %s\n", c.name,
              reg_same ? "PASS" : "FAIL");
  g_ok = g_ok && reg_same;
}

}  // namespace

int main() {
  try {
    // 20 rows of 6400 bytes in 2-hash buckets: a 3 x 3 grid with empty cells in most buckets.
    run({"grid 3x3 with missing cells", 20, 2, 6400, 3, 3, 1, true}, 1);
    // One row per plaintext, a bigger grid and sparse buckets.
    run({"grid with r1,r2 > 1, bigger rows", 40, 3, 15000, 0, 0, 1, true}, 2);
    // One cell per bucket.
    run({"grid 1x1", 4, 1, 3000, 1, 1, 1, false}, 3);
    // More than one query ciphertext: buckets > ring_dim / (r1 + r2).
    run({"more than one query ciphertext", 1500, 1400, 19000, 0, 0, 2, false}, 4);
  } catch (const std::exception& e) {
    std::printf("error (%s): FAIL\n", typeid(e).name());
    return 2;
  }
  std::printf("RESULT %s\n", g_ok ? "PASS" : "FAIL");
  return g_ok ? 0 : 1;
}
