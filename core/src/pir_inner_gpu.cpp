// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/core/pir_inner_gpu.hpp"

#include <memory>
#include <stdexcept>
#include <string>

#include "ombra/core/ciphertext_ops.hpp"
#include "ombra/core/security_floor_context.hpp"

namespace ombra::core {
namespace {

using namespace lbcrypto;
using u64 = std::uint64_t;

static_assert(sizeof(NativeInteger) == sizeof(u64), "OpenFHE must be built with NATIVE_SIZE=64");

[[noreturn]] void fail(const std::string& what) {
  throw std::invalid_argument("pir inner tables: " + what);
}

std::uint32_t bit_reverse(std::uint32_t x, std::uint32_t bits) {
  std::uint32_t r = 0;
  for (std::uint32_t i = 0; i < bits; ++i) r |= ((x >> i) & 1u) << (bits - 1 - i);
  return r;
}

u64 mulmod(u64 a, u64 b, u64 q) {
  return static_cast<u64>((static_cast<unsigned __int128>(a) * b) % q);
}

}  // namespace

EncodeTables extract_pir_inner_tables(const CryptoContext<DCRTPoly>& cc,
                                      const Ciphertext<DCRTPoly>& sample) {
  require_security_floor(cc);
  if (cc->getSchemeId() != SCHEME::BFVRNS_SCHEME) fail("only BFV is supported");
  if (!sample || sample->GetElements().size() != 2) fail("ciphertext must have 2 components");
  const DCRTPoly& c0 = sample->GetElements()[0];
  if (c0.GetFormat() != Format::EVALUATION) fail("ciphertext is not in evaluation form");

  EncodeTables t;
  t.ring_dim = c0.GetRingDimension();
  t.num_towers = c0.GetNumOfElements();
  const std::size_t n = t.ring_dim;
  std::uint32_t logn = 0;
  while ((std::size_t{1} << logn) < n) ++logn;
  // OpenFHE's ChineseRemainderTransformFTTNat::GetTables builds rootReverse[bitrev(i)] = psi^i with
  // psi = the tower's root of unity (transformnat-impl.h:743); same construction as
  // extract_expansion_tables.
  const auto& towers = c0.GetParams()->GetParams();
  if (towers.size() != t.num_towers) fail("tower count");
  for (std::size_t x = 0; x < t.num_towers; ++x) {
    const u64 q = towers[x]->GetModulus().ConvertToInt<u64>();
    const u64 psi = towers[x]->GetRootOfUnity().ConvertToInt<u64>();
    if (towers[x]->GetRingDimension() != n) fail("tower ring dimension");
    t.moduli.push_back(q);
    const std::size_t base = x * n;
    t.psi_rev.resize(base + n);
    u64 a = 1;
    for (std::size_t i = 0; i < n; ++i) {
      t.psi_rev[base + bit_reverse(static_cast<std::uint32_t>(i), logn)] = a;
      a = mulmod(a, psi, q);
    }
  }
  return t;
}

std::vector<u64> flatten_pir_ciphertexts(const std::vector<Ciphertext<DCRTPoly>>& cts,
                                         const EncodeTables& tables) {
  const std::size_t n = tables.ring_dim, nt = tables.num_towers;
  std::vector<u64> words;
  words.reserve(cts.size() * 2 * nt * n);
  for (const auto& ct : cts) {
    if (!ct || ct->GetElements().size() != 2) {
      throw std::invalid_argument("pir inner: a ciphertext does not have 2 components");
    }
    for (const DCRTPoly& poly : ct->GetElements()) {
      if (poly.GetFormat() != Format::EVALUATION) {
        throw std::invalid_argument("pir inner: a ciphertext is not in evaluation form");
      }
      if (poly.GetNumOfElements() != nt || poly.GetRingDimension() != n) {
        throw std::invalid_argument("pir inner: a ciphertext has an unexpected shape");
      }
      for (std::size_t x = 0; x < nt; ++x) {
        const auto& tower = poly.GetElementAtIndex(x);
        const u64 q = tower.GetModulus().ConvertToInt<u64>();
        if (q != tables.moduli[x]) {
          throw std::invalid_argument("pir inner: a ciphertext has other moduli than the tables");
        }
        const auto& v = tower.GetValues();
        for (std::size_t i = 0; i < n; ++i) {
          const u64 w = v[i].ConvertToInt<u64>();
          if (w >= q) throw std::invalid_argument("pir inner: an unreduced ciphertext word");
          words.push_back(w);
        }
      }
    }
  }
  return words;
}

Ciphertext<DCRTPoly> pir_ciphertext_from_words(const Ciphertext<DCRTPoly>& like, const u64* words,
                                               std::size_t count) {
  return unflatten_ciphertext(like, std::vector<u64>(words, words + count));
}

}  // namespace ombra::core
