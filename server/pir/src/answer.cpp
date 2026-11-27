// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/pir/answer.hpp"

#include <algorithm>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "ombra/core/security_floor_context.hpp"
#include "ombra/pir/preset.hpp"
#include "ombra/pir/retrieval.hpp"
#include "openfhe.h"
#include "scheme/bfvrns/bfvrns-ser.h"

namespace ombra::pir {

using namespace lbcrypto;
using Ctx = CryptoContext<DCRTPoly>;
using Impl = CryptoContextImpl<DCRTPoly>;

namespace {

using Clock = std::chrono::steady_clock;
double ms_since(Clock::time_point t) {
  return std::chrono::duration<double, std::milli>(Clock::now() - t).count();
}

SecurityLevel security_level(const std::string& s) {
  if (s == "HEStd_128_classic") return HEStd_128_classic;
  if (s == "HEStd_192_classic") return HEStd_192_classic;
  if (s == "HEStd_256_classic") return HEStd_256_classic;
  if (s == "HEStd_128_quantum") return HEStd_128_quantum;
  if (s == "HEStd_192_quantum") return HEStd_192_quantum;
  if (s == "HEStd_256_quantum") return HEStd_256_quantum;
  throw PirContextError("preset: security level below 128 bits or unknown");  // never NotSet
}

KeySwitchTechnique key_switch(const std::string& s) {
  if (s == "BV") return BV;
  if (s == "HYBRID") return HYBRID;
  throw PirContextError("preset: unknown key switch technique");
}

// The context of the preset, built the way client/ombra/params.py builds it (make_bfv_params).
Ctx build_preset_context() {
  CCParams<CryptoContextBFVRNS> params;
  params.SetSecurityLevel(security_level(preset::kSecurityLevel));
  params.SetRingDim(preset::kRingDim);
  params.SetPlaintextModulus(preset::kPlaintextModulus);
  params.SetMultiplicativeDepth(preset::kMultiplicativeDepth);
  params.SetKeySwitchTechnique(key_switch(preset::kKeySwitchTechnique));
  params.SetDigitSize(preset::kDigitSize);
  auto cc = GenCryptoContext(params);
  cc->Enable(PKE);
  cc->Enable(KEYSWITCH);
  cc->Enable(LEVELEDSHE);
  return cc;
}

const Ctx& reference_context() {
  static std::mutex mu;
  static Ctx ref;
  std::lock_guard g(mu);
  if (!ref) ref = build_preset_context();
  return ref;
}

std::vector<std::string> tower_moduli(const std::shared_ptr<ILDCRTParams<BigInteger>>& p) {
  std::vector<std::string> out;
  if (!p) return out;
  for (const auto& t : p->GetParams()) out.push_back(t->GetModulus().ToString());
  return out;
}

std::string as_string(const std::vector<std::uint8_t>& b) {
  return std::string(reinterpret_cast<const char*>(b.data()), b.size());
}

std::vector<std::int64_t> centered(const std::vector<std::int64_t>& v, std::uint64_t t) {
  std::vector<std::int64_t> out(v.size());
  for (std::size_t i = 0; i < v.size(); ++i)
    out[i] =
        (static_cast<std::uint64_t>(v[i]) > t / 2) ? v[i] - static_cast<std::int64_t>(t) : v[i];
  return out;
}

}  // namespace

PirParams preset_params() { return PirParams{preset::kRingDim, preset::kPlaintextModulus}; }

PirContext read_pir_context(const std::vector<std::uint8_t>& payload) {
  Ctx cc;
  try {
    std::istringstream in(as_string(payload), std::ios::in | std::ios::binary);
    Serial::Deserialize(cc, in, SerType::BINARY);
  } catch (const std::exception&) {
    throw PirContextError("cannot read the retrieval crypto context");
  }
  if (!cc) throw PirContextError("cannot read the retrieval crypto context");
  try {
    if (cc->getSchemeId() != SCHEME::BFVRNS_SCHEME)
      throw PirContextError("the retrieval context is not BFV");
    const auto rns = std::dynamic_pointer_cast<CryptoParametersRNS>(cc->GetCryptoParameters());
    if (!rns) throw PirContextError("the retrieval context is not an RNS scheme");
    // The floor first: below 128 bits is refused whatever else matches.
    try {
      core::require_security_floor(cc);
    } catch (const std::invalid_argument& e) {
      throw PirContextError(std::string("the retrieval context is below the security floor: ") +
                            e.what());
    }
    if (rns->GetStdLevel() != security_level(preset::kSecurityLevel))
      throw PirContextError("the retrieval context has another security level than the preset");
    if (cc->GetRingDimension() != preset::kRingDim)
      throw PirContextError("the retrieval context has another ring dimension than the preset");
    if (cc->GetEncodingParams()->GetPlaintextModulus() != preset::kPlaintextModulus)
      throw PirContextError("the retrieval context has another plaintext modulus than the preset");
    const Ctx& ref = reference_context();
    const auto ref_rns = std::dynamic_pointer_cast<CryptoParametersRNS>(ref->GetCryptoParameters());
    if (rns->GetKeySwitchTechnique() != ref_rns->GetKeySwitchTechnique() ||
        rns->GetDigitSize() != preset::kDigitSize)
      throw PirContextError("the retrieval context has another key switching than the preset");
    if (tower_moduli(rns->GetElementParams()) != tower_moduli(ref_rns->GetElementParams()) ||
        tower_moduli(rns->GetParamsP()) != tower_moduli(ref_rns->GetParamsP()))
      throw PirContextError("the retrieval context has other moduli than the preset");
  } catch (const PirContextError&) {
    throw;
  } catch (const std::exception&) {
    throw PirContextError("the retrieval context does not match the preset");
  }
  return cc;
}

// ---------------------------------------------------------------------------------------------
// Keys

namespace {

void drop_new_entries(const std::set<std::string>& mult_before,
                      const std::set<std::string>& auto_before) {
  std::vector<std::string> gone;
  for (const auto& kv : Impl::GetAllEvalMultKeys())
    if (!mult_before.count(kv.first)) gone.push_back(kv.first);
  for (const auto& t : gone) Impl::ClearEvalMultKeys(t);
  gone.clear();
  for (const auto& kv : Impl::GetAllEvalAutomorphismKeys())
    if (!auto_before.count(kv.first)) gone.push_back(kv.first);
  for (const auto& t : gone) Impl::ClearEvalAutomorphismKeys(t);
}

}  // namespace

PirKeys::PirKeys(const PirContext& cc, const std::vector<std::uint8_t>& mult_payload,
                 const std::vector<std::uint8_t>& automorphism_payload,
                 const std::string& expected_tag, const PirShape& shape) {
  if (!cc) throw PirKeyTagMismatch("no retrieval context");
  std::set<std::string> mult_before, auto_before;
  for (const auto& kv : Impl::GetAllEvalMultKeys()) mult_before.insert(kv.first);
  for (const auto& kv : Impl::GetAllEvalAutomorphismKeys()) auto_before.insert(kv.first);
  try {
    {
      std::istringstream in(as_string(mult_payload), std::ios::in | std::ios::binary);
      if (!Impl::DeserializeEvalMultKey(in, SerType::BINARY))
        throw PirKeyTagMismatch("cannot read the retrieval relinearization key");
    }
    {
      std::istringstream in(as_string(automorphism_payload), std::ios::in | std::ios::binary);
      if (!Impl::DeserializeEvalAutomorphismKey(in, SerType::BINARY))
        throw PirKeyTagMismatch("cannot read the retrieval automorphism keys");
    }
    // Exactly the expected tag was added, to both stores.
    std::set<std::string> added_mult, added_auto;
    for (const auto& kv : Impl::GetAllEvalMultKeys())
      if (!mult_before.count(kv.first)) added_mult.insert(kv.first);
    for (const auto& kv : Impl::GetAllEvalAutomorphismKeys())
      if (!auto_before.count(kv.first)) added_auto.insert(kv.first);
    const std::set<std::string> want{expected_tag};
    if (added_mult != want || added_auto != want)
      throw PirKeyTagMismatch("the retrieval keys carry another key tag than the one named");
    const auto& relin = Impl::GetEvalMultKeyVector(expected_tag);
    if (relin.empty()) throw PirKeyTagMismatch("the retrieval relinearization key is empty");
    const auto& amap = Impl::GetEvalAutomorphismKeyMap(expected_tag);
    for (const std::uint32_t idx : shape.automorphism_indices())
      if (!amap.count(idx))
        throw PirKeyTagMismatch("the retrieval automorphism keys lack an index this call needs");
    tag_ = expected_tag;
  } catch (const PirKeyTagMismatch&) {
    drop_new_entries(mult_before, auto_before);
    throw;
  } catch (const std::exception&) {
    // OpenFHE refuses to insert keys under a tag that already holds keys, and cereal throws on
    // damaged input: either way nothing of this call stays in the stores.
    drop_new_entries(mult_before, auto_before);
    throw PirKeyTagMismatch(
        "the retrieval keys cannot be loaded (another key already holds the tag, or damaged)");
  }
}

PirKeys::~PirKeys() {
  if (tag_.empty()) return;
  try {
    Impl::ClearEvalMultKeys(tag_);
    Impl::ClearEvalAutomorphismKeys(tag_);
  } catch (...) {
  }
}

// ---------------------------------------------------------------------------------------------
// Answer

namespace {

class Answerer {
 public:
  Answerer(const PirContext& cc, const PirShape& shape, const std::uint8_t* body,
           const std::string& tag, PirStageTimes* times = nullptr)
      : cc_(cc), s_(shape), body_(body), tag_(tag), t_(shape.pp.plain_modulus), times_(times) {
    zero_ = plain(std::vector<std::int64_t>(s_.pp.ring_dim, 0));
  }

  Plaintext plain(const std::vector<std::int64_t>& coeffs) const {
    return cc_->MakeCoefPackedPlaintext(centered(coeffs, t_));
  }

  Ciphertext<DCRTPoly> read_query(const std::string& raw) const {
    Ciphertext<DCRTPoly> ct;
    try {
      std::istringstream in(raw, std::ios::in | std::ios::binary);
      Serial::Deserialize(ct, in, SerType::BINARY);
    } catch (const std::exception&) {
      throw PirQueryError("cannot read a query ciphertext");
    }
    if (!ct) throw PirQueryError("cannot read a query ciphertext");
    if (ct->GetKeyTag() != tag_)
      throw PirQueryError("a query was made under another key than the retrieval keys");
    if (ct->GetCryptoContext() != cc_ || ct->NumberCiphertextElements() != 2)
      throw PirQueryError("a query does not belong to the retrieval context");
    const auto towers = cc_->GetElementParams()->GetParams().size();
    for (const auto& e : ct->GetElements())
      if (e.GetNumOfElements() != towers || e.GetRingDimension() != s_.pp.ring_dim)
        throw PirQueryError("a query has an unexpected shape");
    return ct;
  }

  // Selectors 0..need-1: selector p encrypts coefficient p of the query (times 2^L).
  std::map<std::size_t, Ciphertext<DCRTPoly>> expand(const Ciphertext<DCRTPoly>& ct,
                                                     std::size_t need) const {
    const std::uint32_t n = s_.pp.ring_dim;
    const auto& keys = Impl::GetEvalAutomorphismKeyMap(ct->GetKeyTag());
    std::map<std::size_t, Ciphertext<DCRTPoly>> nodes;
    nodes[0] = ct;
    for (std::size_t j = 0; j < s_.levels; ++j) {
      std::vector<std::int64_t> shift(n, 0);
      shift[n - (std::size_t{1} << j)] = 1;  // +x^(N-2^j); negated below
      const Plaintext mono = plain(shift);
      std::map<std::size_t, Ciphertext<DCRTPoly>> nxt;
      for (const auto& [r, c] : nodes) {
        const auto tau =
            cc_->EvalAutomorphism(c, static_cast<std::uint32_t>(n / (1u << j) + 1), keys);
        nxt[r] = cc_->EvalAdd(c, tau);
        if (r + (std::size_t{1} << j) < need)
          nxt[r + (std::size_t{1} << j)] =
              cc_->EvalNegate(cc_->EvalMult(cc_->EvalSub(c, tau), mono));
      }
      nodes.clear();
      for (auto& [r, c] : nxt)
        if (r < need) nodes.emplace(r, std::move(c));
    }
    return nodes;
  }

  // The coefficients ([0, t)) of grid cell p of a bucket; empty for a missing cell.
  std::vector<std::int64_t> cell_coeffs(const std::vector<std::uint64_t>& rows,
                                        std::size_t p) const {
    const std::size_t lo = p * s_.rows_per_pt;
    if (lo >= rows.size()) return {};
    const std::size_t hi = std::min(rows.size(), lo + s_.rows_per_pt);
    std::vector<std::uint8_t> data;
    data.reserve((hi - lo) * s_.row_size);
    for (std::size_t k = lo; k < hi; ++k) {
      const std::uint8_t* r = body_ + rows[k] * s_.row_size;
      data.insert(data.end(), r, r + s_.row_size);
    }
    return bytes_to_coeffs(data.data(), data.size(), s_.pp);
  }

  // The plaintext of grid cell p of a bucket; missing cells are zero.
  Plaintext cell(const std::vector<std::uint64_t>& rows, std::size_t p) const {
    const auto c = cell_coeffs(rows, p);
    return c.empty() ? zero_ : plain(c);
  }

  // The inner sums through the hook: selectors and flat [0, t) cells in, r2 ciphertexts out.
  std::vector<Ciphertext<DCRTPoly>> hooked_inner(
      const InnerSums& hook, const std::map<std::size_t, Ciphertext<DCRTPoly>>& sel,
      std::size_t base, const std::vector<std::uint64_t>& rows) const {
    const std::size_t n = s_.pp.ring_dim;
    std::vector<Ciphertext<DCRTPoly>> selectors;
    selectors.reserve(s_.r1);
    for (std::size_t a = 0; a < s_.r1; ++a) selectors.push_back(sel.at(base + a));
    std::vector<std::int64_t> cells(s_.r1 * s_.r2 * n, 0);
    for (std::size_t p = 0; p < s_.r1 * s_.r2; ++p) {
      const auto c = cell_coeffs(rows, p);
      if (!c.empty()) std::copy(c.begin(), c.end(), cells.begin() + p * n);
    }
    auto inner = hook(cc_, selectors, cells, s_.r1, s_.r2);
    if (inner.size() != s_.r2) throw PirError("the inner sums hook returned a wrong count");
    return inner;
  }

  // Adds the time since `t` to `*slot` (if timing) and restarts the clock.
  void lap(Clock::time_point& t, double PirStageTimes::*slot) const {
    if (!times_) return;
    times_->*slot += ms_since(t);
    t = Clock::now();
  }

  std::string answer_bucket(const std::map<std::size_t, Ciphertext<DCRTPoly>>& sel,
                            std::size_t base, std::size_t bucket, const InnerSums& hook = nullptr,
                            const SecondDim& second = nullptr) const {
    const auto& rows = s_.contents[bucket];
    auto t0 = Clock::now();
    std::vector<Ciphertext<DCRTPoly>> hooked;
    std::vector<Plaintext> cells;
    if (hook) {
      hooked = hooked_inner(hook, sel, base, rows);
    } else {
      cells.reserve(s_.r1 * s_.r2);
      for (std::size_t p = 0; p < s_.r1 * s_.r2; ++p) cells.push_back(cell(rows, p));
    }
    if (hook) lap(t0, &PirStageTimes::first_dim_ms);
    Ciphertext<DCRTPoly> total;
    bool total_done = false;
    if (hook && second) {
      std::vector<Ciphertext<DCRTPoly>> sel2;
      sel2.reserve(s_.r2);
      for (std::size_t b = 0; b < s_.r2; ++b) sel2.push_back(sel.at(base + s_.r1 + b));
      total = second(cc_, sel2, hooked);  // empty: the CPU loop below does the bucket
      total_done = static_cast<bool>(total);
      if (total_done) lap(t0, &PirStageTimes::second_dim_ms);
    }
    for (std::size_t b = 0; b < s_.r2 && !total_done; ++b) {
      Ciphertext<DCRTPoly> inner;
      if (hook) {
        inner = hooked[b];
      } else {
        if (b == 0) lap(t0, &PirStageTimes::first_dim_ms);  // cell encoding
        for (std::size_t a = 0; a < s_.r1; ++a) {
          const auto term = cc_->EvalMult(sel.at(base + a), cells[a * s_.r2 + b]);
          inner = inner ? cc_->EvalAdd(inner, term) : term;
        }
        lap(t0, &PirStageTimes::first_dim_ms);
      }
      const auto prod = cc_->EvalMultNoRelin(sel.at(base + s_.r1 + b), inner);
      total = total ? cc_->EvalAdd(total, prod) : prod;
      lap(t0, &PirStageTimes::second_dim_ms);
    }
    const auto ans = cc_->Compress(cc_->Relinearize(total), 1);
    lap(t0, &PirStageTimes::compress_ms);
    std::ostringstream out(std::ios::out | std::ios::binary);
    Serial::Serialize(ans, out, SerType::BINARY);
    std::string bytes = out.str();
    lap(t0, &PirStageTimes::serialize_ms);
    return bytes;
  }

 private:
  Ctx cc_;
  const PirShape& s_;
  const std::uint8_t* body_;
  std::string tag_;
  std::uint64_t t_;
  Plaintext zero_;
  PirStageTimes* times_;
};

}  // namespace

void answer_queries(const PirContext& cc, const PirKeys& keys, const PirShape& shape,
                    const std::uint8_t* column_body, const std::vector<std::string>& queries,
                    const AnswerSink& sink, const InnerSums& inner_sums, PirStageTimes* times,
                    const SecondDim& second_dim) {
  const auto call_start = Clock::now();
  if (queries.size() != shape.n_cts) throw PirError("wrong number of query ciphertexts");
  if (keys.tag().empty()) throw PirKeyTagMismatch("no retrieval keys are loaded");
  if (times) {
    *times = PirStageTimes{};
    times->rows = shape.n_rows;
    times->buckets = shape.buckets;
    times->n_cts = shape.n_cts;
    times->r1 = shape.r1;
    times->r2 = shape.r2;
  }
  Answerer a(cc, shape, column_body, keys.tag(), times);
  // Every query is read and checked before the first answer, so a bad one never follows answers.
  std::vector<Ciphertext<DCRTPoly>> cts;
  cts.reserve(queries.size());
  for (const auto& q : queries) cts.push_back(a.read_query(q));
  if (times) times->read_ms = ms_since(call_start);
  for (std::size_t c = 0; c < cts.size(); ++c) {
    const auto& ct = cts[c];
    const std::size_t nb = shape.buckets_in_ct(c);
    const auto t_exp = Clock::now();
    const auto sel = a.expand(ct, nb * shape.coeffs_per_bucket());
    if (times) times->expand_ms += ms_since(t_exp);
    for (std::size_t slot = 0; slot < nb; ++slot) {
      const std::size_t bucket = c * shape.per_ct + slot;
      sink(bucket,
           a.answer_bucket(sel, slot * shape.coeffs_per_bucket(), bucket, inner_sums, second_dim));
    }
  }
  if (times) times->total_ms = ms_since(call_start);
}

}  // namespace ombra::pir

namespace ombra::pir {

void check_retrieval_keys(const PirKeyFiles& files, const std::string& key_tag,
                          const PirShape& shape) {
  const PirContext cc = read_pir_context(files.context);
  PirKeys keys(cc, files.eval_mult, files.automorphism, key_tag, shape);
}

namespace {

std::mutex g_factory_mu;
InnerSumsFactory g_factory;
SecondDimFactory g_second_factory;

}  // namespace

void set_gpu_inner_sums_factory(InnerSumsFactory factory) {
  std::lock_guard lk(g_factory_mu);
  g_factory = std::move(factory);
}

void set_gpu_second_dim_factory(SecondDimFactory factory) {
  std::lock_guard lk(g_factory_mu);
  g_second_factory = std::move(factory);
}

std::vector<std::string> answer_call_with_hook(const PirKeyFiles& files, const std::string& key_tag,
                                               const PirShape& shape,
                                               const std::uint8_t* column_body,
                                               const std::vector<std::string>& queries,
                                               const InnerSums& inner_sums, PirStageTimes* times,
                                               const SecondDim& second_dim) {
  const auto call_start = Clock::now();
  const PirContext cc = read_pir_context(files.context);
  PirKeys keys(cc, files.eval_mult, files.automorphism, key_tag, shape);
  const double setup_ms = ms_since(call_start);
  std::vector<std::string> out(shape.buckets);
  answer_queries(
      cc, keys, shape, column_body, queries,
      [&](std::size_t b, std::string&& a) { out.at(b) = std::move(a); }, inner_sums, times,
      second_dim);
  if (times) {
    times->setup_ms = setup_ms;
    times->total_ms = ms_since(call_start);
  }
  return out;
}

std::vector<std::string> answer_call(const PirKeyFiles& files, const std::string& key_tag,
                                     const PirShape& shape, const std::uint8_t* column_body,
                                     const std::vector<std::string>& queries, PirBackend backend,
                                     PirStageTimes* times) {
  InnerSums hook;
  SecondDim second;
  if (backend == PirBackend::kGpu) {
    InnerSumsFactory f;
    SecondDimFactory f2;
    {
      std::lock_guard lk(g_factory_mu);
      f = g_factory;
      f2 = g_second_factory;
    }
    if (f) {
      hook = f();
      if (f2) second = f2();
    }
  }
  return answer_call_with_hook(files, key_tag, shape, column_body, queries, hook, times, second);
}

}  // namespace ombra::pir
