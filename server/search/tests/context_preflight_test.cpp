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
#include <sstream>
#include <string>
#include <vector>

#include "ciphertext-ser.h"
#include "coll_keys.hpp"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "ombra/core/file_envelope.hpp"
#include "ombra/search/session.hpp"
#include "ombra/storage/store.hpp"
#include "openfhe.h"
#include "scheme/ckksrns/ckksrns-ser.h"

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

  // 4. Key files, from the structure templates. A file of the right shape is built from the
  // template (zero coefficients, tag 'a' x 32): it is accepted for every number of keys. Then a
  // byte is flipped at every structure position (all of them for the EvalMult key and for 2 or 3
  // keys; every 97th for the largest file) and each change is refused; a byte in a coefficient
  // array is accepted. Real files of the Python client are tested in
  // tests/test_openfhe_key_templates.py, through the tool search_key_file_check.
  for (const char* p : kPresets) {
    for (const bool is_auto : {false, true}) {
      const auto kind = is_auto ? det::KeyKind::Automorphism : det::KeyKind::EvalMult;
      const std::size_t top = det::describe_key_file(p, kind, 1).max_keys;
      for (const std::size_t n :
           is_auto ? std::vector<std::size_t>{1, 2, 3, top} : std::vector<std::size_t>{1}) {
        const auto map = det::describe_key_file(p, kind, n);
        std::vector<std::uint8_t> file(map.size, 0);
        det::conform_key_file_to_template(p, kind, file);
        for (const auto at : map.tag_offsets)
          std::fill(file.begin() + static_cast<long>(at), file.begin() + static_cast<long>(at + 32),
                    'a');
        det::require_key_file(p, kind, file);
        std::vector<bool> is_free(file.size(), false);
        for (const auto& [a, b] : map.free_ranges)
          for (std::size_t i = a; i < b; ++i) is_free[i] = true;
        // Coefficient words may hold anything.
        for (const auto& [a, b] : map.free_ranges) {
          file[a] = 0xFF;
          file[b - 1] = 0xFF;
          file[(a + b) / 2] ^= 0x5A;
        }
        det::require_key_file(p, kind, file);
        std::vector<std::size_t> structure;
        for (std::size_t i = 0; i < file.size(); ++i)
          if (!is_free[i]) structure.push_back(i);
        const std::size_t step = n == top && is_auto ? 97 : 1;
        std::size_t refused = 0;
        for (std::size_t k = 0; k < structure.size(); k += step) {
          file[structure[k]] ^= 0x01;
          CHECK(!refusal([&] { det::require_key_file(p, kind, file); }).empty());
          file[structure[k]] ^= 0x01;
          ++refused;
        }
        CHECK(refused > 100);
        // A coefficient byte flipped is accepted.
        file[map.free_ranges[0].first + 3] ^= 0x01;
        det::require_key_file(p, kind, file);
        file[map.free_ranges[0].first + 3] ^= 0x01;
        // Tag: not hex, and two different tags.
        for (const auto at : map.tag_offsets) {
          file[at] = 'T';
          CHECK(!refusal([&] { det::require_key_file(p, kind, file); }).empty());
          file[at] = 'A';  // upper case is not a tag OpenFHE writes
          CHECK(!refusal([&] { det::require_key_file(p, kind, file); }).empty());
          file[at] = 'a';
        }
        // Size: one byte more or less, empty, another number of keys' size.
        auto longer = file;
        longer.push_back(0);
        CHECK(!refusal([&] { det::require_key_file(p, kind, longer); }).empty());
        auto shorter = file;
        shorter.pop_back();
        CHECK(!refusal([&] { det::require_key_file(p, kind, shorter); }).empty());
        CHECK(!refusal([&] { det::require_key_file(p, kind, {}); }).empty());
        CHECK(!refusal([&] {
                 det::require_key_file(p, kind, std::vector<std::uint8_t>(35, 7));
               }).empty());
      }
      // An automorphism file with more than log2(N) keys is refused, and the two kinds do not mix.
      if (is_auto) {
        const auto big = det::describe_key_file(p, kind, top);
        const auto small = det::describe_key_file(p, kind, 2);
        std::vector<std::uint8_t> file(
            big.size + (small.size - det::describe_key_file(p, kind, 1).size), 0);
        CHECK(!refusal([&] { det::require_key_file(p, kind, file); }).empty());
        std::vector<std::uint8_t> as_mult(det::describe_key_file(p, det::KeyKind::EvalMult, 1).size,
                                          0);
        det::conform_key_file_to_template(p, det::KeyKind::EvalMult, as_mult);
        CHECK(!refusal([&] { det::require_key_file(p, kind, as_mult); }).empty());
      }
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
  // 6. A key set made by OpenFHE in this process (a synthetic key that lives only here) with the
  // structure bytes set to what the Python client writes: accepted, and OpenFHE reads it. Then the
  // input that once made cereal allocate over 2 GB (fuzz/known/): a valid size, with damaged bytes
  // where the context copy at the start of the key file is. It is refused before OpenFHE reads it.
  {
    const std::string p = "m0_compact_n8192";
    using namespace lbcrypto;
    CryptoContext<DCRTPoly> cc;
    {
      const auto& ctx = *det::canonical_client_context(p);
      std::istringstream in(std::string(ctx.begin(), ctx.end()), std::ios::binary);
      Serial::Deserialize(cc, in, SerType::BINARY);
    }
    const auto kp = cc->KeyGen();
    const std::string tag = kp.publicKey->GetKeyTag();
    cc->EvalMultKeyGen(kp.secretKey);
    const std::uint32_t n = cc->GetRingDimension();
    cc->EvalAutomorphismKeyGen(kp.secretKey, {n + 1, n / 2 + 1});
    std::ostringstream om(std::ios::binary), oa(std::ios::binary);
    CryptoContextImpl<DCRTPoly>::SerializeEvalMultKey(om, SerType::BINARY, tag);
    CryptoContextImpl<DCRTPoly>::SerializeEvalAutomorphismKey(oa, SerType::BINARY, tag);
    const auto as_bytes = [](const std::string& s) {
      return std::vector<std::uint8_t>(s.begin(), s.end());
    };
    auto mult = as_bytes(om.str());
    auto aut = as_bytes(oa.str());
    CryptoContextImpl<DCRTPoly>::ClearEvalMultKeys(tag);
    CryptoContextImpl<DCRTPoly>::ClearEvalAutomorphismKeys(tag);
    det::conform_key_file_to_template(p, det::KeyKind::EvalMult, mult);
    det::conform_key_file_to_template(p, det::KeyKind::Automorphism, aut);
    det::require_key_file(p, det::KeyKind::EvalMult, mult);
    det::require_key_file(p, det::KeyKind::Automorphism, aut);

    ombra::storage::Store store(dir, kOfhe);
    ombra::storage::CollectionSpec spec;
    spec.name = "real_keys";
    spec.preset = p;
    spec.dimension = 8;
    spec.security_level = 1;
    spec.key_tag = tag;
    spec.cluster_count = 4;
    store.create_collection(spec);
    using K = core::EnvelopeKind;
    ombra::search::EngineConfig cfg;
    const auto prepare = [&](const std::vector<std::uint8_t>& m,
                             const std::vector<std::uint8_t>& a) {
      det::CollKeys keys;
      det::prepare_collection_keys(keys, store, cfg, spec.name,
                                   env(K::Context, p, *det::canonical_client_context(p)),
                                   env(K::PublicEvalKey, p, m), env(K::PublicEvalKey, p, a));
      CHECK(keys.cc && keys.akeys.size() == 2);
    };
    prepare(mult, aut);

    std::ifstream in(std::string(OMBRA_FUZZ_KNOWN_DIR) + "/keys-oom-evalmult-body.bin",
                     std::ios::binary);
    const std::vector<std::uint8_t> known((std::istreambuf_iterator<char>(in)), {});
    CHECK(known.size() == 35);
    // The fuzz target `keys` reads byte 0: bit 3 set, file 1 (EvalMult): the rest overwrites the
    // key file from byte 840 on.
    CHECK((known[0] & 3) == 1 && (known[0] & 8));
    auto hostile = mult;
    for (std::size_t i = 1; i < known.size(); ++i) hostile[840 + i - 1] = known[i];
    const std::string why =
        refusal([&] { det::require_key_file(p, det::KeyKind::EvalMult, hostile); });
    CHECK(why.find("does not match the structure") != std::string::npos);
    CHECK(refusal([&] { prepare(hostile, aut); }) == why);
    // The valid set still loads afterwards.
    prepare(mult, aut);
  }
  fs::remove_all(dir);
  std::puts("context preflight tests passed");
  return 0;
}
