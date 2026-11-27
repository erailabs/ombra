// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Where the time of one retrieval answer goes, per stage (PirStageTimes). Not a test: it is built
// with the others but not registered with ctest. Synthetic rows and random selectors (the cost does
// not depend on which cell a query selects). Usage:
//   pir_stage_bench [--rows N[,N...]] [--row-size B] [--m K] [--reps R] [--gpu]
// Defaults: rows 10000,100000, row size 3072, m 10 (15 buckets), 3 repetitions. --gpu needs the
// CUDA build and puts the first-dimension inner sums on the GPU (one untimed warm-up call first).
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "ombra/pir/answer.hpp"
#include "ombra/pir/preset.hpp"
#include "openfhe.h"
#include "scheme/bfvrns/bfvrns-ser.h"
#ifdef OMBRA_PIR_BENCH_GPU
#include "ombra/pir/gpu_inner.hpp"
#endif

using namespace lbcrypto;
using namespace ombra::pir;
using Bytes = std::vector<std::uint8_t>;
using Impl = CryptoContextImpl<DCRTPoly>;

namespace {

template <class T>
Bytes ser(const T& x) {
  std::ostringstream out(std::ios::out | std::ios::binary);
  Serial::Serialize(x, out, SerType::BINARY);
  const std::string s = out.str();
  return Bytes(s.begin(), s.end());
}

double median(std::vector<double> v) {
  std::sort(v.begin(), v.end());
  return v[v.size() / 2];
}

std::vector<std::size_t> parse_list(const char* s) {
  std::vector<std::size_t> out;
  std::stringstream ss(s);
  std::string tok;
  while (std::getline(ss, tok, ',')) out.push_back(std::stoull(tok));
  return out;
}

}  // namespace

int main(int argc, char** argv) {
  std::vector<std::size_t> rows = {10000, 100000};
  std::size_t row_size = 3072, reps = 3;
  std::uint32_t m = 10;
  bool gpu = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    if (a == "--rows" && i + 1 < argc) {
      rows = parse_list(argv[++i]);
    } else if (a == "--row-size" && i + 1 < argc) {
      row_size = std::stoull(argv[++i]);
    } else if (a == "--m" && i + 1 < argc) {
      m = static_cast<std::uint32_t>(std::stoul(argv[++i]));
    } else if (a == "--reps" && i + 1 < argc) {
      reps = std::stoull(argv[++i]);
    } else if (a == "--gpu") {
      gpu = true;
    } else {
      std::fprintf(stderr, "unknown argument %s\n", a.c_str());
      return 2;
    }
  }
#ifndef OMBRA_PIR_BENCH_GPU
  if (gpu) {
    std::fprintf(stderr, "--gpu needs the CUDA build\n");
    return 2;
  }
#endif

  // The context of the preset, as the client builds it (client/ombra/params.py).
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
  const PirParams pp = preset_params();
  const Bytes seed(16, 7);

  for (const std::size_t n_rows : rows) {
    const PirShape shape = make_shape(n_rows, m, row_size, seed, pp);
    std::mt19937_64 rng(n_rows);
    Bytes body(n_rows * row_size);
    for (auto& b : body) b = static_cast<std::uint8_t>(rng());

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

    const std::int64_t t = static_cast<std::int64_t>(pp.plain_modulus);
    std::int64_t inv = 1;  // 2^-L mod t
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

    const PirBackend backend = gpu ? PirBackend::kGpu : PirBackend::kCpu;
#ifdef OMBRA_PIR_BENCH_GPU
    if (gpu) register_gpu_inner_sums();
#endif
    if (gpu) answer_call(files, tag, shape, body.data(), queries, backend);  // warm-up
    std::vector<PirStageTimes> runs;
    for (std::size_t r = 0; r < reps; ++r) {
      PirStageTimes st;
      answer_call(files, tag, shape, body.data(), queries, backend, &st);
      runs.push_back(st);
    }
    auto med = [&](double PirStageTimes::*f) {
      std::vector<double> v;
      for (const auto& r : runs) v.push_back(r.*f);
      return median(v);
    };
    struct Row {
      const char* name;
      double PirStageTimes::*f;
    };
    const Row table[] = {{"setup (context+keys)", &PirStageTimes::setup_ms},
                         {"read queries", &PirStageTimes::read_ms},
                         {"expansion", &PirStageTimes::expand_ms},
                         {"first dimension", &PirStageTimes::first_dim_ms},
                         {"second dimension", &PirStageTimes::second_dim_ms},
                         {"compression", &PirStageTimes::compress_ms},
                         {"serialization", &PirStageTimes::serialize_ms}};
    const double total = med(&PirStageTimes::total_ms);
    std::printf(
        "\nrows=%zu row_size=%zu m=%u buckets=%zu n_cts=%zu r1=%zu r2=%zu backend=%s reps=%zu\n",
        n_rows, row_size, m, shape.buckets, shape.n_cts, shape.r1, shape.r2,
        gpu ? "gpu-inner" : "cpu", reps);
    std::printf("%-22s %12s %8s\n", "stage", "median ms", "% call");
    double accounted = 0;
    for (const auto& r : table) {
      const double v = med(r.f);
      accounted += v;
      std::printf("%-22s %12.1f %7.1f%%\n", r.name, v, 100.0 * v / total);
    }
    std::printf("%-22s %12.1f %7.1f%%\n", "other (sink, glue)", total - accounted,
                100.0 * (total - accounted) / total);
    std::printf("%-22s %12.1f %7.1f%%\n", "total", total, 100.0);
    const double not_gpu = med(&PirStageTimes::expand_ms) + med(&PirStageTimes::compress_ms) +
                           med(&PirStageTimes::serialize_ms);
    std::printf("expansion+compression+serialization: %.1f ms = %.1f%% of the call\n", not_gpu,
                100.0 * not_gpu / total);
  }
  return 0;
}
