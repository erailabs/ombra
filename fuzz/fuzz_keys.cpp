// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Fuzz target: prepare_collection_keys (server/search/src/key_prep.cpp), the one place where the
// bytes of an uploaded crypto context and key files reach OpenFHE (cereal). The preflight must
// refuse everything that is not the preset's context and every key file of a size no key has, so
// no hostile byte reaches the deserializer; what passes the preflight is parsed by OpenFHE.
//
// Setup, once: a store with one collection of the compact preset, and a real key set for it made
// by OpenFHE with a throw-away key (synthetic, lives only in this process; nothing is written).
// Real key files are 1.5 MB (EvalMult) and 3 MB (two automorphism keys), too big for a corpus, so:
//   byte 0, bits 0-1: which file the input replaces: 0 context, 1 EvalMult, 2 automorphism keys,
//                     3 none (control: the valid set must be accepted)
//   byte 0, bit 2:    for a key file, overwrite the START of the valid file with the input (the
//                     size stays valid; the header check is what is fuzzed)
//   byte 0, bit 3:    for a key file, overwrite the valid file from the end of its header
//                     (kKeyHeaderLen) on: the header and size pass the preflight, so OpenFHE parses
//                     the damaged polynomials; with bits 2 and 3 clear the input is the whole
//                     payload (the size check is what is fuzzed). Inputs are at most 64 KiB.
// The corpus holds 2 KiB of the real key files at those offsets, written by
// OMBRA_FUZZ_DUMP_SEEDS=<dir>.
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "coll_keys.hpp"
#include "context_preflight.hpp"
#include "cryptocontext-ser.h"
#include "fuzz_util.hpp"
#include "key/key-ser.h"
#include "ombra/core/file_envelope.hpp"
#include "ombra/search/session.hpp"
#include "ombra/storage/store.hpp"
#include "openfhe.h"
#include "scheme/ckksrns/ckksrns-ser.h"

namespace core = ombra::core;
namespace det = ombra::search::detail;
using namespace lbcrypto;

static ombra::fuzz::TempDir* g_dir = nullptr;
static ombra::storage::Store* g_store = nullptr;
static ombra::search::EngineConfig g_cfg;
using Bytes = std::vector<std::uint8_t>;
static Bytes g_ctx, g_mult, g_auto;

static Bytes bytes_of(const std::string& s) { return Bytes(s.begin(), s.end()); }

extern "C" int LLVMFuzzerInitialize(int*, char***) {
  g_dir = new ombra::fuzz::TempDir();
  g_store = new ombra::storage::Store(g_dir->path() + "/data", ombra::fuzz::kOfhe);
  g_ctx = *det::canonical_context(ombra::fuzz::kPreset);

  CryptoContext<DCRTPoly> cc;
  {
    std::istringstream in(std::string(g_ctx.begin(), g_ctx.end()), std::ios::binary);
    Serial::Deserialize(cc, in, SerType::BINARY);
  }
  const auto kp = cc->KeyGen();
  const std::string tag = kp.publicKey->GetKeyTag();
  cc->EvalMultKeyGen(kp.secretKey);
  const std::uint32_t n = cc->GetRingDimension();
  cc->EvalAutomorphismKeyGen(kp.secretKey, {n + 1, n / 2 + 1});
  {
    std::ostringstream out(std::ios::binary);
    CryptoContextImpl<DCRTPoly>::SerializeEvalMultKey(out, SerType::BINARY, tag);
    g_mult = bytes_of(out.str());
  }
  {
    std::ostringstream out(std::ios::binary);
    CryptoContextImpl<DCRTPoly>::SerializeEvalAutomorphismKey(out, SerType::BINARY, tag);
    g_auto = bytes_of(out.str());
  }
  CryptoContextImpl<DCRTPoly>::ClearEvalMultKeys(tag);
  CryptoContextImpl<DCRTPoly>::ClearEvalAutomorphismKeys(tag);

  // The context copy at the start of a key file is written by this C++ process with 7 class-version
  // words that the Python client writes differently (see context_preflight.hpp). A client's file
  // is what the server accepts, so copy the client's bytes of the header (all but the tag).
  const auto* pp = det::find_preset_params(ombra::fuzz::kPreset);
  const auto as_client = [&](Bytes& file, const unsigned char* tmpl) {
    const std::size_t n = det::kKeyHeaderLen;
    std::vector<bool> is_tag(n, false);
    for (std::size_t i = 0; i + 32 <= n; ++i)
      if (std::equal(tag.begin(), tag.end(), file.begin() + static_cast<std::ptrdiff_t>(i)))
        for (std::size_t j = 0; j < 32; ++j) is_tag[i + j] = true;
    for (std::size_t i = 0; i < n; ++i)
      if (!is_tag[i] && !tmpl[n + i]) file[i] = tmpl[i];
  };
  as_client(g_mult, pp->evalmult_header);
  as_client(g_auto, pp->auto_header);

  ombra::storage::CollectionSpec spec;
  spec.name = "docs";
  spec.preset = ombra::fuzz::kPreset;
  spec.dimension = 8;
  spec.security_level = 1;
  spec.key_tag = tag;
  spec.cluster_count = 4;
  g_store->create_collection(spec);

  if (const char* dump = std::getenv("OMBRA_FUZZ_DUMP_SEEDS")) {
    const std::string d = dump;
    const auto put = [&](const char* name, std::uint8_t mode, const Bytes& v, std::size_t n2) {
      Bytes o{mode};
      const std::size_t from = (mode & 8) ? det::kKeyHeaderLen : 0;
      o.insert(o.end(), v.begin() + static_cast<std::ptrdiff_t>(from),
               v.begin() + static_cast<std::ptrdiff_t>(std::min(from + n2, v.size())));
      ombra::fuzz::write_file(d + "/" + name, o.data(), o.size());
    };
    put("context", 0, g_ctx, g_ctx.size());
    put("evalmult_header", 1 | 4, g_mult, 1024);
    put("automorphism_header", 2 | 4, g_auto, 1024);
    put("evalmult_body", 1 | 8, g_mult, 2048);
    put("automorphism_body", 2 | 8, g_auto, 2048);
    put("control", 3, Bytes{}, 0);
  }
  return 0;
}

static Bytes envelope(core::EnvelopeKind k, const Bytes& payload) {
  return core::write_envelope(core::Envelope{k, ombra::fuzz::kPreset, ombra::fuzz::kOfhe, payload});
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* d, std::size_t n) {
  if (n < 1) return 0;
  const unsigned target = d[0] & 3;
  const bool overwrite = (d[0] & 12) != 0;
  const std::size_t start = (d[0] & 8) ? det::kKeyHeaderLen : 0;
  const Bytes in(d + 1, d + n);
  Bytes ctx = g_ctx, mult = g_mult, aut = g_auto;
  Bytes* t = target == 0 ? &ctx : target == 1 ? &mult : target == 2 ? &aut : nullptr;
  if (t) {
    if (overwrite && target != 0) {
      for (std::size_t i = 0; i < in.size() && start + i < t->size(); ++i) (*t)[start + i] = in[i];
    } else {
      *t = in;
    }
  }
  det::CollKeys keys;
  try {
    det::prepare_collection_keys(keys, *g_store, g_cfg, "docs",
                                 envelope(core::EnvelopeKind::Context, ctx),
                                 envelope(core::EnvelopeKind::PublicEvalKey, mult),
                                 envelope(core::EnvelopeKind::PublicEvalKey, aut));
    if (target == 3 && (!keys.cc || keys.akeys.empty())) std::abort();  // control must succeed
  } catch (const std::exception& e) {
    if (target == 3) {  // the unchanged valid set was refused
      std::fprintf(stderr, "control refused: %s\n", e.what());
      std::abort();
    }
  }
  return 0;
}
