// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// ombra_pir_product_check: exact oracle of the GPU second-dimension product of the retrieval
// answer.
//
// Usage: ombra_pir_product_check [r2 seed [inject]]      (defaults 3 1)
//
// Builds the retrieval BFV context of the preset (pir_bfv_n8192), a key pair (in this process
// only) and r2 pairs of synthetic ciphertexts: x_b encrypts a scalar in {0, 1} (a selector), y_b is
// a random plaintext times a selector (an inner sum, built as the answer builds it, with EvalMult
// by a plaintext). Then
//   1. total = sum_b EvalMultNoRelin(x_b, y_b) with OpenFHE, and on the GPU (PirProductDevice):
//      every RNS word of the three components must be equal, and the GPU total put back into an
//      OpenFHE ciphertext must be identical to OpenFHE's (metadata, format and words);
//   2. the rest of the answer on OpenFHE (Relinearize, Compress to 1 tower): the serialized
//      answers must be byte-identical.
// Prints only check names, PASS/FAIL and counts. Exit code: 0 all pass, 1 a check failed, 2 error.
//
// inject (negative test): "word" adds 1 to one GPU output word; it must make checks fail.

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "ombra/core/oracle_compare.hpp"
#include "ombra/core/pir_inner_gpu.hpp"
#include "ombra/core/pir_product.hpp"
#include "openfhe.h"
#include "pir_inner_context.hpp"
#include "scheme/bfvrns/bfvrns-ser.h"

using namespace lbcrypto;
using ombra::core::pir_test::centered;
using Ct = Ciphertext<DCRTPoly>;

namespace {

bool g_ok = true;

void report(const std::string& name, bool pass, const std::string& detail = "") {
  std::printf("%s%s%s: %s\n", name.c_str(), detail.empty() ? "" : " ", detail.c_str(),
              pass ? "PASS" : "FAIL");
  g_ok = g_ok && pass;
}

std::string serialize(const Ct& ct) {
  std::ostringstream out(std::ios::out | std::ios::binary);
  Serial::Serialize(ct, out, SerType::BINARY);
  return out.str();
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 1 && argc != 3 && argc != 4) {
    std::fprintf(stderr, "usage: ombra_pir_product_check [r2 seed [inject]]\n");
    return 2;
  }
  try {
    const std::size_t r2 = argc >= 3 ? std::stoul(argv[1]) : 3;
    const std::uint64_t seed = argc >= 3 ? std::stoull(argv[2]) : 1;
    const std::string inject = argc == 4 ? argv[3] : "";
    if (r2 == 0 || (!inject.empty() && inject != "word")) {
      std::fprintf(stderr, "bad arguments\n");
      return 2;
    }

    auto cc = ombra::core::pir_test::build_preset_context();
    const std::size_t n = cc->GetRingDimension();
    const std::uint64_t t = cc->GetEncodingParams()->GetPlaintextModulus();
    const auto kp = cc->KeyGen();  // secret key: this process only, never written or printed
    cc->EvalMultKeyGen(kp.secretKey);
    std::mt19937_64 rng(seed);

    std::vector<Ct> xs, ys;
    for (std::size_t b = 0; b < r2; ++b) {
      xs.push_back(cc->Encrypt(
          kp.publicKey, cc->MakeCoefPackedPlaintext({static_cast<std::int64_t>(rng() & 1)})));
      const Ct sel = cc->Encrypt(
          kp.publicKey, cc->MakeCoefPackedPlaintext({static_cast<std::int64_t>(rng() & 1)}));
      std::vector<std::int64_t> raw(n);
      for (auto& v : raw) v = static_cast<std::int64_t>(rng() % t);
      ys.push_back(cc->EvalMult(sel, cc->MakeCoefPackedPlaintext(centered(raw, t))));
    }

    // OpenFHE.
    Ct cpu_total;
    for (std::size_t b = 0; b < r2; ++b) {
      const auto prod = cc->EvalMultNoRelin(xs[b], ys[b]);
      cpu_total = cpu_total ? cc->EvalAdd(cpu_total, prod) : prod;
    }

    // GPU, on raw words.
    const auto tables = ombra::core::extract_pir_product_tables(cc);
    ombra::core::PirProductDevice dev(tables, r2);
    ombra::core::EncodeTables enc;
    enc.ring_dim = n;
    enc.num_towers = tables.nq;
    enc.moduli.assign(tables.moduli.begin(), tables.moduli.begin() + tables.nq);
    const auto xw = ombra::core::flatten_pir_ciphertexts(xs, enc);
    const auto yw = ombra::core::flatten_pir_ciphertexts(ys, enc);
    std::vector<std::uint64_t> out;
    dev.product_sum(xw.data(), yw.data(), out);
    if (inject == "word") out[out.size() / 3] = (out[out.size() / 3] + 1) % tables.moduli[0];

    const Ct gpu_total = ombra::core::pir_product_from_words(xs[0], out.data(), out.size());
    std::size_t bad = 0;
    for (std::size_t c = 0; c < 3; ++c) {
      for (std::size_t x = 0; x < tables.nq; ++x) {
        const auto& v = cpu_total->GetElements()[c].GetElementAtIndex(x).GetValues();
        for (std::size_t i = 0; i < n; ++i) {
          bad += v[i].ConvertToInt<std::uint64_t>() != out[(c * tables.nq + x) * n + i];
        }
      }
    }
    report("GPU total words equal OpenFHE EvalMultNoRelin+EvalAdd, exact", bad == 0,
           "(" + std::to_string(out.size()) + " words, " + std::to_string(bad) + " differ)");
    report("GPU total identical to OpenFHE's (metadata, format, words)",
           ombra::core::identical_ciphertexts(cpu_total, gpu_total));

    const Ct cpu_answer = cc->Compress(cc->Relinearize(cpu_total), 1);
    const Ct gpu_answer = cc->Compress(cc->Relinearize(gpu_total), 1);
    const std::string cb = serialize(cpu_answer), gb = serialize(gpu_answer);
    report("answers identical as ciphertexts",
           ombra::core::identical_ciphertexts(cpu_answer, gpu_answer));
    report("serialized answers byte-identical", cb == gb,
           "(" + std::to_string(cb.size()) + " bytes)");

    std::printf("%s\n", g_ok ? "RESULT PASS" : "RESULT FAIL");
    return g_ok ? 0 : 1;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 2;
  }
}
