// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/core/pir_inner_gpu.hpp"

#include <memory>
#include <stdexcept>
#include <string>

#include "ombra/core/ciphertext_ops.hpp"
#include "ombra/core/security_floor_context.hpp"
#include "scheme/bfvrns/bfvrns-cryptoparameters.h"

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

namespace {

std::vector<u64> flat_u64(const std::vector<NativeInteger>& v) {
  std::vector<u64> out;
  for (const auto& x : v) out.push_back(x.ConvertToInt<u64>());
  return out;
}

// Row-major flattening, checking the shape.
std::vector<u64> flat_u64(const std::vector<std::vector<NativeInteger>>& m, std::size_t rows,
                          std::size_t cols) {
  if (m.size() != rows) fail("table row count");
  std::vector<u64> out;
  for (const auto& r : m) {
    if (r.size() != cols) fail("table column count");
    for (const auto& x : r) out.push_back(x.ConvertToInt<u64>());
  }
  return out;
}

u64 pow_mod(u64 b, u64 e, u64 q) {
  u64 r = 1;
  for (; e; e >>= 1, b = mulmod(b, b, q)) {
    if (e & 1) r = mulmod(r, b, q);
  }
  return r;
}

}  // namespace

PirProductTables extract_pir_product_tables(const CryptoContext<DCRTPoly>& cc) {
  require_security_floor(cc);
  if (cc->getSchemeId() != SCHEME::BFVRNS_SCHEME) fail("only BFV is supported");
  const auto p = std::dynamic_pointer_cast<CryptoParametersBFVRNS>(cc->GetCryptoParameters());
  if (!p) fail("not a BFV context");
  if (p->GetMultiplicationTechnique() != HPSPOVERQLEVELED) {
    fail("only the HPSPOVERQLEVELED multiplication technique is supported");
  }
  PirProductTables t;
  const auto& q_towers = p->GetElementParams()->GetParams();
  t.nq = q_towers.size();
  const std::size_t l = t.nq - 1;  // top level: no levels dropped (see the header)
  const auto& r_towers = p->GetParamsRl(l)->GetParams();
  t.nr = r_towers.size();
  t.ring_dim = p->GetElementParams()->GetRingDimension();
  const std::size_t n = t.ring_dim;
  std::uint32_t logn = 0;
  while ((std::size_t{1} << logn) < n) ++logn;
  std::vector<std::shared_ptr<ILNativeParams>> towers(q_towers.begin(), q_towers.end());
  towers.insert(towers.end(), r_towers.begin(), r_towers.end());
  for (std::size_t x = 0; x < towers.size(); ++x) {
    const u64 q = towers[x]->GetModulus().ConvertToInt<u64>();
    const u64 psi = towers[x]->GetRootOfUnity().ConvertToInt<u64>();
    if (towers[x]->GetRingDimension() != n) fail("tower ring dimension");
    const u64 psi_inv = pow_mod(psi, q - 2, q);  // q is prime
    t.moduli.push_back(q);
    t.n_inv.push_back(pow_mod(static_cast<u64>(n), q - 2, q));
    t.psi_rev.resize((x + 1) * n);
    t.psi_inv_rev.resize((x + 1) * n);
    u64 a = 1, b = 1;
    for (std::size_t i = 0; i < n; ++i) {
      const auto pos = bit_reverse(static_cast<std::uint32_t>(i), logn);
      t.psi_rev[x * n + pos] = a;
      t.psi_inv_rev[x * n + pos] = b;
      a = mulmod(a, psi, q);
      b = mulmod(b, psi_inv, q);
    }
  }
  // The tables the CPU path of LeveledSHEBFVRNS::EvalMult reads (bfvrns-leveledshe.cpp), by name.
  t.ex_hat_inv = flat_u64(p->GetQlHatInvModq(l));
  t.ex_hat_mod = flat_u64(p->GetQlHatModr(l), t.nr, t.nq);
  t.ex_alpha = flat_u64(p->GetalphaQlModr(l), t.nq + 1, t.nr);
  t.q_inv = p->GetqInv();
  t.b_hat_inv = flat_u64(p->GetmNegRlQHatInvModq(l));
  t.b_conv = flat_u64(p->GetqInvModr(), t.nq, t.nr);
  t.r_hat_inv = flat_u64(p->GetRlHatInvModr(l));
  t.r_hat_mod = flat_u64(p->GetRlHatModq(l), t.nq, t.nr);
  t.r_alpha = flat_u64(p->GetalphaRlModq(l), t.nr + 1, t.nq);
  t.r_inv = p->GetrInv();
  t.sr_tab = flat_u64(p->GettQlSlHatInvModsDivsModq(l), t.nq, t.nr + 1);
  t.sr_frac = p->GettQlSlHatInvModsDivsFrac(l);
  if (t.ex_hat_inv.size() != t.nq || t.b_hat_inv.size() != t.nq || t.r_hat_inv.size() != t.nr ||
      t.q_inv.size() < t.nq || t.r_inv.size() < t.nr || t.sr_frac.size() != t.nr) {
    fail("table sizes");
  }
  t.q_inv.resize(t.nq);
  t.r_inv.resize(t.nr);
  return t;
}

Ciphertext<DCRTPoly> pir_product_from_words(const Ciphertext<DCRTPoly>& like, const u64* words,
                                            std::size_t count) {
  const DCRTPoly& first = like->GetElements()[0];
  const std::size_t nt = first.GetNumOfElements(), n = first.GetRingDimension();
  if (count != 3 * nt * n) throw std::invalid_argument("pir product: wrong word count");
  Ciphertext<DCRTPoly> result =
      like->Clone();  // keeps the metadata a serialized ciphertext carries
  // OpenFHE's ScaleAndRound builds its output on the parameters GetParamsQl(l) of the context; they
  // serialize differently from the ciphertext's own, so the total must carry them too.
  const auto crypto =
      std::dynamic_pointer_cast<CryptoParametersBFVRNS>(like->GetCryptoParameters());
  if (!crypto) throw std::invalid_argument("pir product: not a BFV ciphertext");
  const auto params = crypto->GetParamsQl(static_cast<uint32_t>(nt - 1));
  std::vector<DCRTPoly> elements;
  for (std::size_t c = 0; c < 3; ++c) {
    DCRTPoly poly(params, Format::COEFFICIENT, true);
    auto& towers = poly.GetAllElements();
    for (std::size_t x = 0; x < nt; ++x) {
      const NativeInteger modulus = first.GetElementAtIndex(x).GetModulus();
      NativeVector v(n, modulus);
      const u64* src = words + (c * nt + x) * n;
      for (std::size_t i = 0; i < n; ++i) v[i] = NativeInteger(src[i]);
      towers[x].SetValues(std::move(v), Format::COEFFICIENT);
    }
    elements.push_back(std::move(poly));
  }
  result->SetElements(std::move(elements));
  result->SetNoiseScaleDeg(like->GetNoiseScaleDeg() + 1);
  return result;
}

}  // namespace ombra::core
