// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// BLAKE2b (RFC 7693) with an optional key, self-contained. It only keys the cuckoo hashing of
// private retrieval with the public per-call seed (the client's hashlib.blake2b call), so it needs
// no secret-dependent timing care.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace ombra::pir {

// BLAKE2b with digest_len in 1..64 bytes and key_len in 0..64 bytes. Throws std::invalid_argument
// outside those ranges.
std::vector<std::uint8_t> blake2b(const std::uint8_t* data, std::size_t size,
                                  const std::uint8_t* key, std::size_t key_len,
                                  std::size_t digest_len);

}  // namespace ombra::pir
