// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The retrieval answer on OpenFHE: context checks, key loading next to other tags, and answers that
// decrypt to the selected grid cell. Key generation and decryption live in this test only (the
// client's role); the library under test never sees a secret key. Synthetic data. The match with
// the Python reference is tests/test_server_pir_retrieve.py.
#include "ombra/pir/answer.hpp"

#include <cstdio>
#include <sstream>
#include <string>
#include <vector>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "ombra/pir/preset.hpp"
#include "openfhe.h"
#include "scheme/bfvrns/bfvrns-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;
using namespace ombra::pir;
using Bytes = std::vector<std::uint8_t>;
using Impl = CryptoContextImpl<DCRTPoly>;

static int failures = 0;
#define CHECK(c)                                                                 \
  do {                                                                           \
    if (!(c)) {                                                                  \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); \
      ++failures;                                                                \
    }                                                                            \
  } while (0)

namespace {

template <class E, class F>
bool throws_type(F&& f) {
  try {
    f();
  } catch (const E&) {
    return true;
  } catch (...) {
  }
  return false;
}

CryptoContext<DCRTPoly> bfv(std::uint64_t t, std::uint32_t digit = preset::kDigitSize) {
  CCParams<CryptoContextBFVRNS> p;
  p.SetSecurityLevel(HEStd_128_classic);
  p.SetRingDim(preset::kRingDim);
  p.SetPlaintextModulus(t);
  p.SetMultiplicativeDepth(preset::kMultiplicativeDepth);
  p.SetKeySwitchTechnique(BV);
  p.SetDigitSize(digit);
  auto cc = GenCryptoContext(p);
  cc->Enable(PKE);
  cc->Enable(KEYSWITCH);
  cc->Enable(LEVELEDSHE);
  return cc;
}

template <class T>
Bytes ser(const T& x) {
  std::ostringstream out(std::ios::out | std::ios::binary);
  Serial::Serialize(x, out, SerType::BINARY);
  const std::string s = out.str();
  return Bytes(s.begin(), s.end());
}

void context_checks() {
  const auto cc = bfv(preset::kPlaintextModulus);
  CHECK(read_pir_context(ser(cc)) != nullptr);
  CHECK(throws_type<PirContextError>([&] { read_pir_context(Bytes{1, 2, 3}); }));
  CHECK(throws_type<PirContextError>([&] { read_pir_context(Bytes{}); }));
  CHECK(throws_type<PirContextError>([&] { read_pir_context(ser(bfv(65537))); }));
  CHECK(throws_type<PirContextError>(
      [&] { read_pir_context(ser(bfv(preset::kPlaintextModulus, 5))); }));
  // Another scheme.
  CCParams<CryptoContextCKKSRNS> p;
  p.SetSecurityLevel(HEStd_NotSet);
  p.SetRingDim(8192);
  p.SetMultiplicativeDepth(2);
  p.SetScalingModSize(40);
  p.SetFirstModSize(55);
  auto ck = GenCryptoContext(p);
  ck->Enable(PKE);
  ck->Enable(KEYSWITCH);
  ck->Enable(LEVELEDSHE);
  CHECK(throws_type<PirContextError>([&] { read_pir_context(ser(ck)); }));
  // Below the floor: no standard level.
  CCParams<CryptoContextBFVRNS> low;
  low.SetSecurityLevel(HEStd_NotSet);
  low.SetRingDim(8192);
  low.SetPlaintextModulus(preset::kPlaintextModulus);
  low.SetMultiplicativeDepth(preset::kMultiplicativeDepth);
  low.SetKeySwitchTechnique(BV);
  low.SetDigitSize(preset::kDigitSize);
  auto weak = GenCryptoContext(low);
  weak->Enable(PKE);
  weak->Enable(KEYSWITCH);
  weak->Enable(LEVELEDSHE);
  CHECK(throws_type<PirContextError>([&] { read_pir_context(ser(weak)); }));
}

void answers_decrypt_to_the_cells() {
  const auto cc = bfv(preset::kPlaintextModulus);
  const PirContext pc = read_pir_context(ser(cc));
  const PirParams pp = preset_params();
  const std::size_t row_size = 6400, n_rows = 20;
  const Bytes seed(16, 7);
  const PirShape shape = make_shape(n_rows, 2, row_size, seed, pp);
  CHECK(shape.r1 == 3 && shape.r2 == 3 && shape.n_cts == 1);

  Bytes body(n_rows * row_size);
  for (std::size_t i = 0; i < body.size(); ++i)
    body[i] = static_cast<std::uint8_t>(i * 131 + (i >> 8));

  // Client side: keys and queries.
  const auto kp = cc->KeyGen();
  cc->EvalMultKeyGen(kp.secretKey);
  cc->EvalAutomorphismKeyGen(kp.secretKey, shape.automorphism_indices());
  const std::string tag = kp.publicKey->GetKeyTag();
  std::ostringstream mo(std::ios::out | std::ios::binary), ao(std::ios::out | std::ios::binary);
  CHECK(Impl::SerializeEvalMultKey(mo, SerType::BINARY, tag));
  CHECK(Impl::SerializeEvalAutomorphismKey(ao, SerType::BINARY, tag));
  const std::string ms = mo.str(), as = ao.str();
  const Bytes mult(ms.begin(), ms.end()), autom(as.begin(), as.end());
  Impl::ClearEvalMultKeys(tag);
  Impl::ClearEvalAutomorphismKeys(tag);

  // Another tag, as a search collection's keys would be: it must survive the whole call.
  const auto other = cc->KeyGen();
  cc->EvalMultKeyGen(other.secretKey);
  const std::string other_tag = other.publicKey->GetKeyTag();
  CHECK(!Impl::GetEvalMultKeyVector(other_tag).empty());

  const std::int64_t t = static_cast<std::int64_t>(pp.plain_modulus);
  std::int64_t inv = 1;  // 2^-L mod t
  for (std::size_t i = 0; i < shape.levels; ++i) inv = inv * ((t + 1) / 2) % t;
  std::vector<std::pair<std::size_t, std::size_t>> want_cell(shape.buckets);
  std::vector<std::int64_t> q(pp.ring_dim, 0);
  for (std::size_t b = 0; b < shape.buckets; ++b) {
    const std::size_t a = (b + 1) % shape.r1, c = (2 * b + 1) % shape.r2;
    want_cell[b] = {a, c};
    const std::size_t base = b * shape.coeffs_per_bucket();
    q[base + a] = inv;
    q[base + shape.r1 + c] = inv;
  }
  for (auto& v : q)
    if (v > t / 2) v -= t;
  const auto ct = cc->Encrypt(kp.publicKey, cc->MakeCoefPackedPlaintext(q));
  const Bytes ct_bytes = ser(ct);
  const std::vector<std::string> queries = {std::string(ct_bytes.begin(), ct_bytes.end())};

  // The wrong tag is refused and leaves the stores as they were.
  CHECK(throws_type<PirKeyTagMismatch>([&] { PirKeys(pc, mult, autom, "not-the-tag", shape); }));
  CHECK(Impl::GetAllEvalMultKeys().count(tag) == 0 &&
        Impl::GetAllEvalMultKeys().count(other_tag) == 1);
  CHECK(throws_type<PirKeyTagMismatch>([&] { PirKeys(pc, Bytes{1, 2}, autom, tag, shape); }));
  CHECK(Impl::GetAllEvalMultKeys().count(tag) == 0 &&
        Impl::GetAllEvalAutomorphismKeys().count(tag) == 0);

  std::vector<std::string> answers(shape.buckets);
  {
    PirKeys keys(pc, mult, autom, tag, shape);
    CHECK(Impl::GetAllEvalMultKeys().count(tag) == 1 &&
          Impl::GetAllEvalMultKeys().count(other_tag) == 1);
    // A second load of the same tag while it is held is refused.
    CHECK(throws_type<PirKeyTagMismatch>([&] { PirKeys(pc, mult, autom, tag, shape); }));
    CHECK(Impl::GetAllEvalMultKeys().count(tag) == 1);
    std::size_t seen = 0;
    answer_queries(pc, keys, shape, body.data(), queries, [&](std::size_t b, std::string&& a) {
      CHECK(b == seen);
      ++seen;
      answers[b] = std::move(a);
    });
    CHECK(seen == shape.buckets);
    CHECK(throws_type<PirError>([&] {
      answer_queries(pc, keys, shape, body.data(), {}, [](std::size_t, std::string&&) {});
    }));
    CHECK(throws_type<PirQueryError>([&] {
      answer_queries(pc, keys, shape, body.data(), {"junk"}, [](std::size_t, std::string&&) {});
    }));
  }
  // Released: the PIR tag is gone, the other tag is intact.
  CHECK(Impl::GetAllEvalMultKeys().count(tag) == 0 &&
        Impl::GetAllEvalAutomorphismKeys().count(tag) == 0);
  CHECK(Impl::GetAllEvalMultKeys().count(other_tag) == 1);

  // Decrypt (client role) and compare with the selected cell.
  for (std::size_t b = 0; b < shape.buckets; ++b) {
    Ciphertext<DCRTPoly> ans;
    std::istringstream in(answers[b], std::ios::in | std::ios::binary);
    Serial::Deserialize(ans, in, SerType::BINARY);
    Plaintext pt;
    cc->Decrypt(kp.secretKey, ans, &pt);
    pt->SetLength(pp.ring_dim);
    const auto got = pt->GetCoefPackedValue();
    const auto [a, c] = want_cell[b];
    const std::size_t p = a * shape.r2 + c;
    Bytes data;
    const auto& rows = shape.contents[b];
    for (std::size_t k = p * shape.rows_per_pt;
         k < std::min(rows.size(), (p + 1) * shape.rows_per_pt); ++k)
      data.insert(data.end(), body.begin() + rows[k] * row_size,
                  body.begin() + (rows[k] + 1) * row_size);
    const auto want = bytes_to_coeffs(data.data(), data.size(), pp);
    bool same = got.size() == want.size();
    for (std::size_t i = 0; same && i < want.size(); ++i) same = ((got[i] % t + t) % t) == want[i];
    CHECK(same);
  }
}

}  // namespace

int main() {
  context_checks();
  answers_decrypt_to_the_cells();
  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::puts("pir answer tests passed");
  return 0;
}
