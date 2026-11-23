// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/pir/layout.hpp"

#include <algorithm>
#include <cmath>

#include "ombra/pir/blake2b.hpp"

namespace ombra::pir {

std::uint32_t PirParams::coeff_bits() const {
  std::uint32_t b = 0;
  for (std::uint64_t t = plain_modulus; t > 1; t >>= 1) ++b;
  return b;
}

std::size_t PirParams::plaintext_bytes() const {
  return static_cast<std::size_t>(ring_dim) * coeff_bits() / 8;
}

std::size_t n_buckets(std::size_t m) { return std::max<std::size_t>(kHashes, (m * 3 + 1) / 2); }

std::vector<std::size_t> candidate_buckets(const std::vector<std::uint8_t>& seed, std::uint64_t row,
                                           std::size_t buckets) {
  if (buckets < kHashes) throw PirError("too few buckets");
  if (seed.size() != kSeedLen) throw PirError("seed must be 16 bytes");
  std::vector<std::size_t> out;
  for (std::uint32_t ctr = 0; out.size() < kHashes; ++ctr) {
    std::uint8_t msg[12];
    for (int i = 0; i < 8; ++i) msg[i] = static_cast<std::uint8_t>(row >> (8 * i));
    for (int i = 0; i < 4; ++i) msg[8 + i] = static_cast<std::uint8_t>(ctr >> (8 * i));
    const auto d = blake2b(msg, sizeof msg, seed.data(), seed.size(), 32);
    for (int w = 0; w < 8 && out.size() < kHashes; ++w) {
      std::uint32_t v = 0;
      for (int i = 3; i >= 0; --i) v = (v << 8) | d[4 * w + i];
      const std::size_t b = v % buckets;
      if (std::find(out.begin(), out.end(), b) == out.end()) out.push_back(b);
    }
  }
  return out;
}

std::vector<std::vector<std::uint64_t>> bucket_contents(const std::vector<std::uint8_t>& seed,
                                                        std::uint64_t n_rows, std::size_t buckets) {
  std::vector<std::vector<std::uint64_t>> contents(buckets);
  for (std::uint64_t i = 0; i < n_rows; ++i)
    for (const std::size_t b : candidate_buckets(seed, i, buckets)) contents[b].push_back(i);
  return contents;
}

std::size_t rows_per_plaintext(std::size_t row_size, const PirParams& pp) {
  if (row_size == 0) throw PirError("row size must be positive");
  const std::size_t n = pp.plaintext_bytes() / row_size;
  if (n < 1) throw PirError("a row does not fit in one plaintext");
  return n;
}

std::vector<std::int64_t> bytes_to_coeffs(const std::uint8_t* data, std::size_t size,
                                          const PirParams& pp) {
  if (size > pp.plaintext_bytes()) throw PirError("data does not fit in one plaintext");
  const std::uint32_t bits = pp.coeff_bits();
  std::vector<std::int64_t> out(pp.ring_dim, 0);
  for (std::size_t i = 0; i < out.size(); ++i) {
    std::int64_t v = 0;
    for (std::uint32_t k = 0; k < bits; ++k) {
      const std::size_t p = i * bits + k;
      const std::size_t byte = p / 8;
      if (byte >= size) break;  // zero padding
      v |= static_cast<std::int64_t>((data[byte] >> (p % 8)) & 1) << k;
    }
    out[i] = v;
  }
  return out;
}

std::size_t PirShape::buckets_in_ct(std::size_t c) const {
  return std::min(per_ct, buckets - c * per_ct);
}

std::vector<std::uint32_t> PirShape::automorphism_indices() const {
  std::vector<std::uint32_t> idx;
  for (std::size_t j = 0; j < levels; ++j)
    idx.push_back(static_cast<std::uint32_t>(pp.ring_dim / (1u << j) + 1));
  return idx;
}

PirShape make_shape(std::uint64_t n_rows, std::uint32_t m, std::size_t row_size,
                    const std::vector<std::uint8_t>& seed, const PirParams& pp) {
  if (n_rows < 1 || m < 1) throw PirError("need at least one row and m >= 1");
  if (seed.size() != kSeedLen) throw PirError("seed must be 16 bytes");
  PirShape s;
  s.n_rows = n_rows;
  s.m = m;
  s.row_size = row_size;
  s.seed = seed;
  s.pp = pp;
  s.rows_per_pt = rows_per_plaintext(row_size, pp);
  s.buckets = n_buckets(m);
  s.contents = bucket_contents(seed, n_rows, s.buckets);
  s.bucket_rows = 1;
  for (const auto& c : s.contents) s.bucket_rows = std::max(s.bucket_rows, c.size());
  s.pts_per_bucket = (s.bucket_rows + s.rows_per_pt - 1) / s.rows_per_pt;
  // r1 = ceil(sqrt(pts)) computed exactly: isqrt(pts - 1) + 1.
  std::size_t root = static_cast<std::size_t>(std::sqrt(static_cast<double>(s.pts_per_bucket - 1)));
  while (root * root > s.pts_per_bucket - 1) --root;
  while ((root + 1) * (root + 1) <= s.pts_per_bucket - 1) ++root;
  s.r1 = root + 1;
  s.r2 = (s.pts_per_bucket + s.r1 - 1) / s.r1;
  const std::size_t width = s.r1 + s.r2;
  s.per_ct = std::min<std::size_t>(s.buckets, pp.ring_dim / width);
  if (s.per_ct < 1) throw PirError("grid too large for one ciphertext");
  s.n_cts = (s.buckets + s.per_ct - 1) / s.per_ct;
  const std::size_t used = s.per_ct * width - 1;
  s.levels = 0;
  for (std::size_t v = used; v > 0; v >>= 1) ++s.levels;
  return s;
}

}  // namespace ombra::pir
