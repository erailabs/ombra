// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Errors of the retrieval answer, without any OpenFHE type, so callers can catch them.
#pragma once

#include <stdexcept>

namespace ombra::pir {

// The context is not the retrieval preset, or is below the security floor.
class PirContextError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};
// The keys are unreadable, incomplete, or made under another key tag than the one named.
class PirKeyTagMismatch : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};
// A query ciphertext is unreadable or does not belong to the context and keys of the call.
class PirQueryError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

}  // namespace ombra::pir
