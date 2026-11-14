// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Diagnostic, NOT part of `make fuzz`: OpenFHE's own deserializers on raw bytes, with no preflight.
// It exists to show why the preflight in key_prep.cpp is needed: this target is expected to find
// crashes in OpenFHE/cereal quickly. Build it with the others; run it by hand:
//   build-fuzz/fuzz/ombra_fuzz_openfhe_raw -max_total_time=60 fuzz/corpus/keys
// Byte 0: 0 context, 1 EvalMult key, 2 automorphism keys.
#include <cstdint>
#include <exception>
#include <sstream>
#include <string>

#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "openfhe.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* d, std::size_t n) {
  if (n < 1) return 0;
  std::istringstream in(std::string(reinterpret_cast<const char*>(d + 1), n - 1), std::ios::binary);
  try {
    switch (d[0] % 3) {
      case 0: {
        CryptoContext<DCRTPoly> cc;
        Serial::Deserialize(cc, in, SerType::BINARY);
        break;
      }
      case 1:
        CryptoContextImpl<DCRTPoly>::DeserializeEvalMultKey(in, SerType::BINARY);
        break;
      default:
        CryptoContextImpl<DCRTPoly>::DeserializeEvalAutomorphismKey(in, SerType::BINARY);
    }
  } catch (const std::exception&) {
  }
  return 0;
}
