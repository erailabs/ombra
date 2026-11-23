// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/pir/blake2b.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <stdexcept>

namespace ombra::pir {

namespace {

constexpr std::array<std::uint64_t, 8> kIv = {
    0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL, 0x3c6ef372fe94f82bULL, 0xa54ff53a5f1d36f1ULL,
    0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL, 0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL};

constexpr std::uint8_t kSigma[12][16] = {{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
                                         {14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3},
                                         {11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4},
                                         {7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8},
                                         {9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13},
                                         {2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9},
                                         {12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11},
                                         {13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10},
                                         {6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5},
                                         {10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0},
                                         {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
                                         {14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3}};

inline std::uint64_t rotr(std::uint64_t x, int n) { return (x >> n) | (x << (64 - n)); }

inline std::uint64_t load_le64(const std::uint8_t* p) {
  std::uint64_t v = 0;
  for (int i = 7; i >= 0; --i) v = (v << 8) | p[i];
  return v;
}

struct State {
  std::array<std::uint64_t, 8> h{};
  std::uint64_t t = 0;  // bytes compressed so far (the high word stays 0 below 2^64 bytes)

  void compress(const std::uint8_t* block, bool last) {
    std::uint64_t m[16];
    for (int i = 0; i < 16; ++i) m[i] = load_le64(block + 8 * i);
    std::uint64_t v[16];
    for (int i = 0; i < 8; ++i) {
      v[i] = h[i];
      v[i + 8] = kIv[i];
    }
    v[12] ^= t;
    if (last) v[14] = ~v[14];
    const auto g = [&](int a, int b, int c, int d, std::uint64_t x, std::uint64_t y) {
      v[a] = v[a] + v[b] + x;
      v[d] = rotr(v[d] ^ v[a], 32);
      v[c] = v[c] + v[d];
      v[b] = rotr(v[b] ^ v[c], 24);
      v[a] = v[a] + v[b] + y;
      v[d] = rotr(v[d] ^ v[a], 16);
      v[c] = v[c] + v[d];
      v[b] = rotr(v[b] ^ v[c], 63);
    };
    for (int r = 0; r < 12; ++r) {
      const std::uint8_t* s = kSigma[r];
      g(0, 4, 8, 12, m[s[0]], m[s[1]]);
      g(1, 5, 9, 13, m[s[2]], m[s[3]]);
      g(2, 6, 10, 14, m[s[4]], m[s[5]]);
      g(3, 7, 11, 15, m[s[6]], m[s[7]]);
      g(0, 5, 10, 15, m[s[8]], m[s[9]]);
      g(1, 6, 11, 12, m[s[10]], m[s[11]]);
      g(2, 7, 8, 13, m[s[12]], m[s[13]]);
      g(3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
    for (int i = 0; i < 8; ++i) h[i] ^= v[i] ^ v[i + 8];
  }
};

}  // namespace

std::vector<std::uint8_t> blake2b(const std::uint8_t* data, std::size_t size,
                                  const std::uint8_t* key, std::size_t key_len,
                                  std::size_t digest_len) {
  if (digest_len < 1 || digest_len > 64) throw std::invalid_argument("blake2b: bad digest length");
  if (key_len > 64) throw std::invalid_argument("blake2b: key too long");
  State st;
  st.h = kIv;
  st.h[0] ^= 0x01010000ULL ^ (static_cast<std::uint64_t>(key_len) << 8) ^ digest_len;

  // The message is the key block (key padded to 128 bytes), if any, then the data. Every block but
  // the last is compressed as it is; the last one (possibly partial and zero padded, and the only
  // one of an empty message) carries the final flag.
  std::array<std::uint8_t, 128> keyblock{};
  const bool keyed = key_len > 0;
  if (keyed) std::memcpy(keyblock.data(), key, key_len);
  const std::size_t total = (keyed ? 128 : 0) + size;

  std::size_t consumed = 0;
  std::array<std::uint8_t, 128> buf;
  while (true) {
    buf.fill(0);
    std::size_t got = 0;
    if (keyed && consumed == 0) {
      buf = keyblock;
      got = 128;
    } else {
      const std::size_t dpos = consumed - (keyed ? 128 : 0);
      got = std::min<std::size_t>(128, size - dpos);
      if (got) std::memcpy(buf.data(), data + dpos, got);
    }
    consumed += got;
    const bool last = consumed >= total;
    st.t += got;
    st.compress(buf.data(), last);
    if (last) break;
  }
  std::vector<std::uint8_t> out(digest_len);
  for (std::size_t i = 0; i < digest_len; ++i)
    out[i] = static_cast<std::uint8_t>(st.h[i / 8] >> (8 * (i % 8)));
  return out;
}

}  // namespace ombra::pir
