// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// OpenFHE glue of PirInnerDevice (pir_inner.hpp): the tables and ciphertext words of a BFV context
// as flat arrays, and the way back from words to an OpenFHE ciphertext. Public material only:
// ciphertexts, no keys.
#pragma once

#include <cstdint>
#include <vector>

#include "ombra/core/plain_search.hpp"  // EncodeTables
#include "openfhe.h"

namespace ombra::core {

// Moduli and NTT tables of the Q towers of `sample` (a 2-component BFV ciphertext of the context).
// Checks the security floor first. Throws std::invalid_argument unless the scheme is BFV and
// `sample` has 2 components in evaluation form.
EncodeTables extract_pir_inner_tables(const lbcrypto::CryptoContext<lbcrypto::DCRTPoly>& cc,
                                      const lbcrypto::Ciphertext<lbcrypto::DCRTPoly>& sample);

// The words of 2-component evaluation-form ciphertexts with the tables' moduli, laid out
// [ciphertext][component][tower][coeff] (what PirInnerDevice::inner takes as selectors). Throws
// std::invalid_argument on any other shape, format, moduli or a word that is not reduced.
std::vector<std::uint64_t> flatten_pir_ciphertexts(
    const std::vector<lbcrypto::Ciphertext<lbcrypto::DCRTPoly>>& cts, const EncodeTables& tables);

// A copy of `like` (context, key tag, level, noise degree, scale) carrying `words`
// ([component][tower][coeff]): the result of a GPU product as an OpenFHE ciphertext. The metadata
// is that of `like`, which is what OpenFHE's EvalMult by a plaintext leaves on a BFV ciphertext;
// ombra_pir_inner_check proves it against OpenFHE.
lbcrypto::Ciphertext<lbcrypto::DCRTPoly> pir_ciphertext_from_words(
    const lbcrypto::Ciphertext<lbcrypto::DCRTPoly>& like, const std::uint64_t* words,
    std::size_t count);

}  // namespace ombra::core
