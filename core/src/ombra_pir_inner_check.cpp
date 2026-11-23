// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// ombra_pir_inner_check: exact oracle of the GPU inner products of the retrieval answer.
//
// Usage: ombra_pir_inner_check [r1 r2 seed [inject]]      (defaults 3 2 1)
//
// Builds the retrieval BFV context of the preset (pir_bfv_n8192), a key pair (in this process
// only), r1 + r2 query ciphertexts and r1 * r2 random cell plaintexts, all synthetic. Then
//   1. inner_b = sum_a EvalMult(sel1_a, cell[a * r2 + b]) with OpenFHE (EvalMult + EvalAdd), and on
//      the GPU (PirInnerDevice); every RNS word of every inner_b must be equal, and the GPU result
//      put back into an OpenFHE ciphertext must be identical to OpenFHE's (metadata and words);
//   2. the rest of the answer on OpenFHE (EvalMultNoRelin by sel2_b, EvalAdd, Relinearize,
//      Compress to 1 tower) for both: the serialized answers must be byte-identical and decrypt to
//      the same plaintext (and to the plaintext computed directly mod t).
// Prints only check names, PASS/FAIL and counts. Exit code: 0 all pass, 1 a check failed, 2 error.
//
// Plaintext lift: MakeCoefPackedPlaintext stores a coefficient modulo t (a negative v as v + t,
// in [0, t)) and EvalMult lifts that value to every tower as is, not centered. The GPU therefore
// gets the cells as values in [0, t) (what bytes_to_coeffs returns before centered()).
//
// inject (negative tests): "word" adds 1 to one GPU output word; "centered" gives the GPU the cell
// values lifted centered (negative for v > t/2) instead of in [0, t). Both must make checks fail.

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
#include "ombra/core/pir_inner.hpp"
#include "ombra/core/pir_inner_gpu.hpp"
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
  if (argc != 1 && argc != 4 && argc != 5) {
    std::fprintf(stderr, "usage: ombra_pir_inner_check [r1 r2 seed [inject]]\n");
    return 2;
  }
  try {
    const std::size_t r1 = argc >= 4 ? std::stoul(argv[1]) : 3;
    const std::size_t r2 = argc >= 4 ? std::stoul(argv[2]) : 2;
    const std::uint64_t seed = argc >= 4 ? std::stoull(argv[3]) : 1;
    const std::string inject = argc == 5 ? argv[4] : "";
    if (r1 == 0 || r2 == 0 || (!inject.empty() && inject != "word" && inject != "centered")) {
      std::fprintf(stderr, "bad arguments\n");
      return 2;
    }

    auto cc = ombra::core::pir_test::build_preset_context();
    const std::size_t n = cc->GetRingDimension();
    const std::uint64_t t = cc->GetEncodingParams()->GetPlaintextModulus();
    const auto kp = cc->KeyGen();  // secret key: this process only, never written or printed
    cc->EvalMultKeyGen(kp.secretKey);
    std::mt19937_64 rng(seed);

    // Synthetic queries: sel1_a encrypts the scalar s_a in {0, 1}, sel2_b the scalar u_b.
    std::vector<std::int64_t> s(r1), u(r2);
    std::vector<Ct> sel1, sel2;
    for (auto& x : s) x = static_cast<std::int64_t>(rng() & 1);
    for (auto& x : u) x = static_cast<std::int64_t>(rng() & 1);
    for (std::size_t a = 0; a < r1; ++a) {
      sel1.push_back(cc->Encrypt(kp.publicKey, cc->MakeCoefPackedPlaintext({s[a]})));
    }
    for (std::size_t b = 0; b < r2; ++b) {
      sel2.push_back(cc->Encrypt(kp.publicKey, cc->MakeCoefPackedPlaintext({u[b]})));
    }

    // Synthetic cells: coefficients uniform in [0, t), given to OpenFHE centered like the answer's
    // cell() does (it stores them modulo t again).
    std::vector<std::int64_t> cell_coeffs(r1 * r2 * n);  // centered, as given to OpenFHE
    std::vector<std::int64_t> cell_raw(r1 * r2 * n);     // [0, t), as OpenFHE stores and lifts
    std::vector<Plaintext> cells;
    for (std::size_t p = 0; p < r1 * r2; ++p) {
      std::vector<std::int64_t> raw(n);
      for (auto& x : raw) x = static_cast<std::int64_t>(rng() % t);
      const std::vector<std::int64_t> c = centered(raw, t);
      std::copy(raw.begin(), raw.end(), cell_raw.begin() + p * n);
      std::copy(c.begin(), c.end(), cell_coeffs.begin() + p * n);
      cells.push_back(cc->MakeCoefPackedPlaintext(c));
    }

    // OpenFHE: the inner ciphertexts.
    std::vector<Ct> cpu_inner;
    for (std::size_t b = 0; b < r2; ++b) {
      Ct inner;
      for (std::size_t a = 0; a < r1; ++a) {
        const auto term = cc->EvalMult(sel1[a], cells[a * r2 + b]);
        inner = inner ? cc->EvalAdd(inner, term) : term;
      }
      cpu_inner.push_back(inner);
    }

    // GPU: the same, on raw words.
    const ombra::core::EncodeTables tables = ombra::core::extract_pir_inner_tables(cc, sel1[0]);
    ombra::core::PirInnerDevice dev(tables, r1, r2);
    const std::vector<std::uint64_t> sel_words = ombra::core::flatten_pir_ciphertexts(sel1, tables);
    std::vector<std::uint64_t> out;
    dev.inner(sel_words.data(), (inject == "centered" ? cell_coeffs : cell_raw).data(), out);
    if (inject == "word") out[out.size() / 3] = (out[out.size() / 3] + 1) % tables.moduli[0];

    // 1. Exact words, and the GPU result as an OpenFHE ciphertext against OpenFHE's.
    const std::size_t ct_words = 2 * dev.poly_words();
    std::size_t word_mismatch_cts = 0, word_mismatches = 0, meta_mismatch_cts = 0;
    std::vector<Ct> gpu_inner;
    for (std::size_t b = 0; b < r2; ++b) {
      const std::vector<std::uint64_t> want =
          ombra::core::flatten_pir_ciphertexts(std::vector<Ct>{cpu_inner[b]}, tables);
      std::size_t bad = 0;
      for (std::size_t i = 0; i < ct_words; ++i) bad += want[i] != out[b * ct_words + i];
      word_mismatches += bad;
      word_mismatch_cts += bad != 0;
      gpu_inner.push_back(
          ombra::core::pir_ciphertext_from_words(sel1[0], out.data() + b * ct_words, ct_words));
      meta_mismatch_cts += !ombra::core::identical_ciphertexts(cpu_inner[b], gpu_inner[b]);
    }
    report("GPU inner words equal OpenFHE EvalMult+EvalAdd, exact", word_mismatches == 0,
           "(" + std::to_string(r2 - word_mismatch_cts) + "/" + std::to_string(r2) +
               " inner ciphertexts, " + std::to_string(r2 * ct_words) + " words)");
    report("GPU inner ciphertexts identical to OpenFHE's (metadata and words)",
           meta_mismatch_cts == 0,
           "(" + std::to_string(r2 - meta_mismatch_cts) + "/" + std::to_string(r2) + ")");

    // 2. The rest of the answer on OpenFHE, from each inner list.
    const auto finish = [&](const std::vector<Ct>& inners) {
      Ct total;
      for (std::size_t b = 0; b < r2; ++b) {
        const auto prod = cc->EvalMultNoRelin(sel2[b], inners[b]);
        total = total ? cc->EvalAdd(total, prod) : prod;
      }
      return cc->Compress(cc->Relinearize(total), 1);
    };
    const Ct cpu_answer = finish(cpu_inner);
    const Ct gpu_answer = finish(gpu_inner);
    const std::string cpu_bytes = serialize(cpu_answer), gpu_bytes = serialize(gpu_answer);
    report("serialized answers byte-identical", cpu_bytes == gpu_bytes,
           "(" + std::to_string(cpu_bytes.size()) + " bytes)");

    Plaintext cpu_pt, gpu_pt;
    cc->Decrypt(kp.secretKey, cpu_answer, &cpu_pt);
    cc->Decrypt(kp.secretKey, gpu_answer, &gpu_pt);
    cpu_pt->SetLength(n);
    gpu_pt->SetLength(n);
    const auto cpu_vals = cpu_pt->GetCoefPackedValue();
    const auto gpu_vals = gpu_pt->GetCoefPackedValue();
    report("answers decrypt to the same plaintext", cpu_vals == gpu_vals);

    // The plaintext from the definition: sum_b u_b sum_a s_a cell(a, b), mod t.
    const std::int64_t ti = static_cast<std::int64_t>(t);
    std::vector<std::int64_t> expect(n, 0);
    for (std::size_t b = 0; b < r2; ++b) {
      for (std::size_t a = 0; a < r1; ++a) {
        if (u[b] == 0 || s[a] == 0) continue;
        for (std::size_t i = 0; i < n; ++i) {
          expect[i] = (expect[i] + cell_coeffs[(a * r2 + b) * n + i]) % ti;
        }
      }
    }
    bool def_ok = gpu_vals.size() == n;
    for (std::size_t i = 0; def_ok && i < n; ++i) {
      def_ok = ((gpu_vals[i] - expect[i]) % ti + ti) % ti == 0;
    }
    report("GPU answer decrypts to the plaintext computed mod t", def_ok);

    std::printf("%s\n", g_ok ? "RESULT PASS" : "RESULT FAIL");
    return g_ok ? 0 : 1;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 2;
  }
}
