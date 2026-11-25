// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The layout of one private-retrieval call, a C++ port of the shared half of client/ombra/pir.py:
// batched cuckoo hashing (each row in 3 candidate buckets, keyed by a public seed), the fixed shape
// of a call (grid, bucket count, query ciphertext count), and how row bytes become plaintext
// coefficients. Plain C++, no OpenFHE. A golden test compares every function with the Python code.
#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace ombra::pir {

// An impossible shape or malformed input (the Python PirError).
class PirError : public std::invalid_argument {
 public:
  using std::invalid_argument::invalid_argument;
};

inline constexpr std::size_t kHashes = 3;    // candidate buckets per row
inline constexpr std::size_t kSeedLen = 16;  // bytes of the public seed

// The numbers of the BFV preset the layout depends on.
struct PirParams {
  std::uint32_t ring_dim = 0;
  std::uint64_t plain_modulus = 0;
  std::uint32_t coeff_bits() const;     // floor(log2 t)
  std::size_t plaintext_bytes() const;  // ring_dim * coeff_bits / 8
};

// buckets = max(3, ceil(1.5 m)).
std::size_t n_buckets(std::size_t m);

// The 3 distinct candidate buckets of a row: keyed BLAKE2b-256 over le64(row) || le32(ctr), the
// 16-byte seed as key, read as 8 little-endian u32 words, each reduced modulo `buckets`.
std::vector<std::size_t> candidate_buckets(const std::vector<std::uint8_t>& seed, std::uint64_t row,
                                           std::size_t buckets);

// Row indices of each bucket, ascending. Every row is in 3 buckets.
std::vector<std::vector<std::uint64_t>> bucket_contents(const std::vector<std::uint8_t>& seed,
                                                        std::uint64_t n_rows, std::size_t buckets);

// Rows never span two plaintexts; a row bigger than one plaintext throws PirError.
std::size_t rows_per_plaintext(std::size_t row_size, const PirParams& pp);

// Little-endian bit stream, coeff_bits per coefficient, zero padded: ring_dim values in [0,
// 2^bits).
std::vector<std::int64_t> bytes_to_coeffs(const std::uint8_t* data, std::size_t size,
                                          const PirParams& pp);

// Everything the sizes of a call depend on: (m, database size, row size, public seed).
struct PirShape {
  std::uint64_t n_rows = 0;
  std::uint32_t m = 0;
  std::size_t row_size = 0;
  std::vector<std::uint8_t> seed;
  PirParams pp;
  std::size_t buckets = 0;
  std::size_t rows_per_pt = 0;
  std::size_t bucket_rows = 0;  // rows of the fullest bucket; every bucket is padded to this
  std::size_t pts_per_bucket = 0;
  std::size_t r1 = 0, r2 = 0;
  std::size_t per_ct = 0;  // buckets sharing one query ciphertext
  std::size_t n_cts = 0;
  std::size_t levels = 0;  // L: expansion levels
  std::vector<std::vector<std::uint64_t>> contents;

  std::size_t coeffs_per_bucket() const { return r1 + r2; }
  std::size_t buckets_in_ct(std::size_t c) const;
  // N / 2^j + 1 for j < levels.
  std::vector<std::uint32_t> automorphism_indices() const;
};

PirShape make_shape(std::uint64_t n_rows, std::uint32_t m, std::size_t row_size,
                    const std::vector<std::uint8_t>& seed, const PirParams& pp);

}  // namespace ombra::pir
