// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Preflight of the context and key files before OpenFHE reads them (server/search/src/
// context_preflight.cpp, used first in prepare_collection_keys). Synthetic data only.
//
// context_golden/<preset>.bin are the contexts the real Python client (openfhe-python 1.6.1,
// client/ombra/params.py make_context) serializes for the two presets.
// tests/test_openfhe_context_canonical.py checks the files against the current client. The server
// writes the same context with seven class-version words that differ (1 instead of 0); the test
// pins that: the two byte strings have equal length and differ only there.
#include "context_preflight.hpp"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "coll_keys.hpp"
#include "ombra/core/file_envelope.hpp"
#include "ombra/search/session.hpp"
#include "ombra/storage/store.hpp"
#include "openfhe.h"

#define CHECK(cond)                                                                \
  do {                                                                             \
    if (!(cond)) {                                                                 \
      std::fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      std::exit(1);                                                                \
    }                                                                              \
  } while (0)

namespace fs = std::filesystem;
namespace core = ombra::core;
namespace det = ombra::search::detail;
using ombra::search::InvalidQuery;

static const char* kOfhe = "1.6.1";
static const char* kPresets[] = {"m0_depth2", "m0_compact_n8192"};

static std::vector<std::uint8_t> read_fixture(const std::string& preset) {
  std::ifstream in(std::string(OMBRA_GOLDEN_DIR) + "/" + preset + ".bin", std::ios::binary);
  CHECK(in.good());
  return std::vector<std::uint8_t>(std::istreambuf_iterator<char>(in), {});
}

// Message of the InvalidQuery that `fn` throws; fails if it throws nothing else or nothing.
template <class F>
static std::string refusal(F fn) {
  try {
    fn();
  } catch (const InvalidQuery& e) {
    return e.what();
  }
  CHECK(false && "expected a refusal");
  return {};
}

static std::vector<std::uint8_t> env(core::EnvelopeKind k, const std::string& preset,
                                     std::vector<std::uint8_t> payload) {
  return core::write_envelope(core::Envelope{k, preset, kOfhe, std::move(payload)});
}

int main() {
  const std::string kRefused =
      "the uploaded crypto context is not the context of the collection's preset";

  // 1. The embedded client context is the real client's file; the server's own serialization has
  // the same length and differs from it only in class-version words (server 1, client 0).
  for (const char* p : kPresets) {
    const auto* own = det::canonical_context(p);
    const auto* client = det::canonical_client_context(p);
    CHECK(own != nullptr && client != nullptr);
    CHECK(*client == read_fixture(p));
    CHECK(own->size() == client->size());
    std::size_t differing = 0;
    for (std::size_t i = 0; i < own->size(); ++i) {
      if ((*own)[i] == (*client)[i]) continue;
      ++differing;
      CHECK((*own)[i] == 1 && (*client)[i] == 0);
    }
    CHECK(differing == 7);
    det::require_canonical_context(p, *own);  // both writers are accepted
    det::require_canonical_context(p, *client);
  }
  CHECK(det::canonical_context("no_such_preset") == nullptr);
  CHECK(refusal([] { det::require_canonical_context("no_such_preset", {}); }) ==
        "the collection's preset has no context on this server");

  // 2. One byte changed anywhere, a different preset's context, truncation, extension, empty.
  for (const char* p : kPresets) {
    const auto good = read_fixture(p);
    // A mix of both writers (client bytes with one version word of the server's) is refused too.
    {
      auto mixed = good;
      const auto& own = *det::canonical_context(p);
      for (std::size_t i = 0; i < good.size(); ++i)
        if (own[i] != good[i]) {
          mixed[i] = own[i];
          break;
        }
      CHECK(refusal([&] { det::require_canonical_context(p, mixed); }) == kRefused);
    }
    for (std::size_t i = 0; i < good.size(); ++i) {
      auto bad = good;
      bad[i] ^= 0x01;
      CHECK(refusal([&] { det::require_canonical_context(p, bad); }) == kRefused);
    }
    auto shorter = good;
    shorter.pop_back();
    CHECK(refusal([&] { det::require_canonical_context(p, shorter); }) == kRefused);
    auto longer = good;
    longer.push_back(0);
    CHECK(refusal([&] { det::require_canonical_context(p, longer); }) == kRefused);
    CHECK(refusal([&] { det::require_canonical_context(p, {}); }) == kRefused);
  }
  CHECK(refusal([] {
          det::require_canonical_context("m0_depth2", read_fixture("m0_compact_n8192"));
        }) == kRefused);

  // 3. A context with lower security, built here by test-only code (the one place a below-floor
  // context may exist: it is only serialized, never used for encryption), is refused as a
  // context that is not the preset's, not later by the security floor.
  {
    lbcrypto::CCParams<lbcrypto::CryptoContextCKKSRNS> params;
    params.SetSecurityLevel(lbcrypto::HEStd_NotSet);
    params.SetRingDim(4096);
    params.SetMultiplicativeDepth(2);
    params.SetScalingModSize(50);
    params.SetFirstModSize(60);
    params.SetBatchSize(2048);
    params.SetScalingTechnique(lbcrypto::FIXEDMANUAL);
    auto cc = lbcrypto::GenCryptoContext(params);
    cc->Enable(lbcrypto::PKE);
    cc->Enable(lbcrypto::KEYSWITCH);
    cc->Enable(lbcrypto::LEVELEDSHE);
    std::ostringstream out(std::ios::out | std::ios::binary);
    lbcrypto::Serial::Serialize(cc, out, lbcrypto::SerType::BINARY);
    const std::string s = out.str();
    const std::vector<std::uint8_t> weak(s.begin(), s.end());
    CHECK(weak != read_fixture("m0_depth2"));
    CHECK(refusal([&] { det::require_canonical_context("m0_depth2", weak); }) == kRefused);
  }

  // 4. Key sizes: the sizes of the real client's files (EvalMult, and automorphism keys for
  // d = 2, 3, 8, 384, 1024 and N/2) are accepted; others are not.
  struct Sizes {
    const char* preset;
    std::size_t mult;
    std::vector<std::size_t> autos;
  };
  const Sizes real[] = {
      {"m0_depth2", 2623379, {5245643, 7867899, 10490155, 26223691, 28845947, 36712715}},
      {"m0_compact_n8192", 1574919, {3148779, 4722631, 6296483, 15739595, 17313447, 20461151}}};
  for (const auto& r : real) {
    det::require_key_size(r.preset, det::KeyKind::EvalMult, r.mult);
    for (auto a : r.autos) det::require_key_size(r.preset, det::KeyKind::Automorphism, a);
    for (const std::size_t bad : {std::size_t{0}, std::size_t{1}, r.mult - 100000,
                                  r.mult * 2 + 100000, std::size_t{1} << 30, ~std::size_t{0}}) {
      CHECK(
          !refusal([&] { det::require_key_size(r.preset, det::KeyKind::EvalMult, bad); }).empty());
      CHECK(!refusal([&] {
               det::require_key_size(r.preset, det::KeyKind::Automorphism, bad);
             }).empty());
    }
    // An automorphism file is not an EvalMult key and the other way round.
    CHECK(!refusal([&] {
             det::require_key_size(r.preset, det::KeyKind::EvalMult, r.autos[1]);
           }).empty());
    // More keys than log2(N) are refused.
    CHECK(!refusal([&] {
             det::require_key_size(r.preset, det::KeyKind::Automorphism, r.autos.back() * 2);
           }).empty());
  }

  // 4b. The start of a key file (key tag and a copy of the context, which OpenFHE parses).
  for (const char* p : kPresets) {
    for (const bool is_auto : {false, true}) {
      const auto kind = is_auto ? det::KeyKind::Automorphism : det::KeyKind::EvalMult;
      std::ifstream in(
          std::string(OMBRA_GOLDEN_DIR) + "/" + p + (is_auto ? ".auto_header" : ".evalmult_header"),
          std::ios::binary);
      const std::vector<std::uint8_t> file((std::istreambuf_iterator<char>(in)), {});
      const std::size_t n = det::kKeyHeaderLen;
      CHECK(file.size() == 2 * n);
      std::vector<std::uint8_t> payload(file.begin(), file.begin() + static_cast<long>(n));
      std::vector<std::size_t> tag_at;
      for (std::size_t i = 0; i + 32 <= n; ++i) {
        bool t = true;
        for (std::size_t j = 0; t && j < 32; ++j) t = payload[i + j] == 'T';
        if (t) {
          tag_at.push_back(i);
          i += 31;
        }
      }
      CHECK(tag_at.size() == 2);
      const auto with_tag = [&](char c) {
        auto v = payload;
        for (auto at : tag_at)
          std::fill(v.begin() + static_cast<long>(at), v.begin() + static_cast<long>(at + 32), c);
        v.resize(n + 5000, 0);  // key data follows
        return v;
      };
      const std::string what =
          is_auto ? "the automorphism key file does not start with the preset's context"
                  : "the EvalMult key file does not start with the preset's context";
      det::require_key_header(p, kind, with_tag('a'));
      CHECK(refusal([&] { det::require_key_header(p, kind, with_tag('T')); }) == what);  // not hex
      auto two_tags = with_tag('a');
      two_tags[tag_at[1]] = 'b';
      CHECK(refusal([&] { det::require_key_header(p, kind, two_tags); }) == what);
      CHECK(refusal([&] { det::require_key_header(p, kind, {}); }) == what);
      std::size_t refused = 0, free_bytes = 0;
      for (std::size_t i = 0; i < n; ++i) {
        const bool in_tag =
            (i >= tag_at[0] && i < tag_at[0] + 32) || (i >= tag_at[1] && i < tag_at[1] + 32);
        if (in_tag) continue;
        auto v = with_tag('a');
        v[i] ^= 0x01;
        try {
          det::require_key_header(p, kind, v);
          CHECK(file[n + i] == 1);  // only a byte the mask frees may change
          ++free_bytes;
        } catch (const InvalidQuery&) {
          ++refused;
        }
      }
      CHECK(refused + free_bytes == n - 64);
      CHECK(free_bytes == (is_auto ? 3u : 0u));
    }
  }

  // 5. prepare_collection_keys refuses before deserializing: a tampered context with garbage
  // behind it, and good context with garbage keys. A valid context passes the first check and
  // stops at the key size.
  const std::string dir = (fs::temp_directory_path() / "ombra_preflight_test").string();
  fs::remove_all(dir);
  {
    ombra::storage::Store store(dir, kOfhe);
    for (const char* p : kPresets) {
      ombra::storage::CollectionSpec spec;
      spec.name = std::string("c_") + p;
      spec.preset = p;
      spec.dimension = 8;
      spec.security_level = 1;
      spec.key_tag = "tag-synthetic";
      spec.cluster_count = 4;
      store.create_collection(spec);

      const auto good = read_fixture(p);
      const std::vector<std::uint8_t> junk(100, 0xAB);
      using K = core::EnvelopeKind;
      ombra::search::EngineConfig cfg;
      const auto prepare = [&](const std::vector<std::uint8_t>& c,
                               const std::vector<std::uint8_t>& m,
                               const std::vector<std::uint8_t>& a) {
        det::CollKeys keys;
        det::prepare_collection_keys(keys, store, cfg, spec.name, c, m, a);
      };
      auto bad = good;
      bad[bad.size() / 2] ^= 0xFF;
      CHECK(refusal([&] {
              prepare(env(K::Context, p, bad), env(K::PublicEvalKey, p, junk),
                      env(K::PublicEvalKey, p, junk));
            }) == kRefused);
      // Context of the other preset inside an envelope that names this one.
      const std::string other = std::string(p) == "m0_depth2" ? "m0_compact_n8192" : "m0_depth2";
      CHECK(refusal([&] {
              prepare(env(K::Context, p, read_fixture(other)), env(K::PublicEvalKey, p, junk),
                      env(K::PublicEvalKey, p, junk));
            }) == kRefused);
      // A valid context gets past the context check and stops at the key size.
      CHECK(refusal([&] {
              prepare(env(K::Context, p, good), env(K::PublicEvalKey, p, junk),
                      env(K::PublicEvalKey, p, junk));
            }) == "the EvalMult key file has a size no key of the collection's preset has");
    }
  }
  fs::remove_all(dir);
  std::puts("context preflight tests passed");
  return 0;
}
