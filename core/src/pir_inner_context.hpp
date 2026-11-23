// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The retrieval BFV context of the preset `pir_bfv_n8192`, for the check and bench binaries of the
// GPU inner products. Built exactly like build_preset_context() in server/pir/src/answer.cpp (and
// client/ombra/params.py make_bfv_params), from the constants of the generated preset header (which
// CMake writes from core/params/ckks_presets.json). No number is chosen here.
#pragma once

#include <stdexcept>
#include <string>

#include "ombra/pir/preset.hpp"
#include "openfhe.h"

namespace ombra::core::pir_test {

inline lbcrypto::SecurityLevel preset_security_level(const std::string& s) {
  using namespace lbcrypto;
  if (s == "HEStd_128_classic") return HEStd_128_classic;
  if (s == "HEStd_192_classic") return HEStd_192_classic;
  if (s == "HEStd_256_classic") return HEStd_256_classic;
  if (s == "HEStd_128_quantum") return HEStd_128_quantum;
  if (s == "HEStd_192_quantum") return HEStd_192_quantum;
  if (s == "HEStd_256_quantum") return HEStd_256_quantum;
  throw std::invalid_argument("preset: security level below 128 bits or unknown");  // never NotSet
}

inline lbcrypto::KeySwitchTechnique preset_key_switch(const std::string& s) {
  if (s == "BV") return lbcrypto::BV;
  if (s == "HYBRID") return lbcrypto::HYBRID;
  throw std::invalid_argument("preset: unknown key switch technique");
}

inline lbcrypto::CryptoContext<lbcrypto::DCRTPoly> build_preset_context() {
  using namespace lbcrypto;
  namespace preset = ombra::pir::preset;
  CCParams<CryptoContextBFVRNS> params;
  params.SetSecurityLevel(preset_security_level(preset::kSecurityLevel));
  params.SetRingDim(preset::kRingDim);
  params.SetPlaintextModulus(preset::kPlaintextModulus);
  params.SetMultiplicativeDepth(preset::kMultiplicativeDepth);
  params.SetKeySwitchTechnique(preset_key_switch(preset::kKeySwitchTechnique));
  params.SetDigitSize(preset::kDigitSize);
  auto cc = GenCryptoContext(params);
  cc->Enable(PKE);
  cc->Enable(KEYSWITCH);
  cc->Enable(LEVELEDSHE);
  return cc;
}

// Coefficients of `v` (values in [0, t)) as centered signed values, as the answer lifts them.
inline std::vector<std::int64_t> centered(const std::vector<std::int64_t>& v, std::uint64_t t) {
  std::vector<std::int64_t> out(v.size());
  for (std::size_t i = 0; i < v.size(); ++i) {
    out[i] =
        (static_cast<std::uint64_t>(v[i]) > t / 2) ? v[i] - static_cast<std::int64_t>(t) : v[i];
  }
  return out;
}

}  // namespace ombra::core::pir_test
