// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// BLAKE2b and the retrieval layout against fixtures written by the Python code
// (tests/generate_golden.py): golden/blake2b.txt (hashlib) and golden/layout.txt
// (client/ombra/pir.py).
//
// layout.txt lines: "params ring t"; "hash <seed hex> <buckets> <n>" then n lines "h <row> b b b";
// "shape n_rows m row_size seed_hex" then "f buckets rows_per_pt bucket_rows pts r1 r2 per_ct n_cts
// levels coeffs_per_bucket | automorphism indices | buckets_in_ct per ct" then "c <bucket> rows..."
// lines (a large call lists a few buckets); "coeffs n sum weighted_sum first24 | last8".
#include "ombra/pir/layout.hpp"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "ombra/pir/blake2b.hpp"
#include "ombra/pir/retrieval.hpp"

using namespace ombra::pir;

static int failures = 0;
#define CHECK(c)                                                                 \
  do {                                                                           \
    if (!(c)) {                                                                  \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); \
      ++failures;                                                                \
    }                                                                            \
  } while (0)

namespace {

using Bytes = std::vector<std::uint8_t>;

Bytes from_hex(const std::string& h) {
  Bytes out;
  if (h == "-") return out;
  for (std::size_t i = 0; i + 1 < h.size(); i += 2)
    out.push_back(static_cast<std::uint8_t>(std::stoi(h.substr(i, 2), nullptr, 16)));
  return out;
}

std::string to_hex(const Bytes& b) {
  static const char* d = "0123456789abcdef";
  std::string s;
  for (auto x : b) {
    s += d[x >> 4];
    s += d[x & 15];
  }
  return s;
}

std::ifstream open(const std::string& name) {
  std::ifstream f(std::string(OMBRA_PIR_GOLDEN_DIR) + "/" + name);
  if (!f) {
    std::fprintf(stderr, "cannot open golden file %s\n", name.c_str());
    std::exit(2);
  }
  return f;
}

void blake2b_vectors() {
  // RFC 7693 appendix A: BLAKE2b-512("abc"), unkeyed.
  const Bytes abc = {'a', 'b', 'c'};
  CHECK(to_hex(blake2b(abc.data(), abc.size(), nullptr, 0, 64)) ==
        "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d1"
        "7d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923");
  // The official keyed vector: key 00..3f, empty input, 64-byte digest.
  Bytes key(64);
  for (std::size_t i = 0; i < 64; ++i) key[i] = static_cast<std::uint8_t>(i);
  CHECK(to_hex(blake2b(nullptr, 0, key.data(), key.size(), 64)) ==
        "10ebb67700b1868efb4417987acf4690ae9d972fb7a590c2f02871799aaa4786"
        "b5e996e8f0f4eb981fc214b005f42d2ff4233499391653df7aefcbc13fc51568");
  bool threw = false;
  try {
    blake2b(nullptr, 0, nullptr, 0, 65);
  } catch (const std::invalid_argument&) {
    threw = true;
  }
  CHECK(threw);

  auto f = open("blake2b.txt");
  std::string k, d, want;
  int n = 0;
  while (f >> k >> d >> want) {
    const Bytes key_b = from_hex(k), data = from_hex(d);
    CHECK(to_hex(blake2b(data.data(), data.size(), key_b.data(), key_b.size(), 32)) == want);
    ++n;
  }
  CHECK(n == 33);
}

std::vector<std::string> split(const std::string& s) {
  std::istringstream in(s);
  std::vector<std::string> v;
  for (std::string t; in >> t;) v.push_back(t);
  return v;
}

void layout_golden() {
  auto f = open("layout.txt");
  std::string line;
  PirParams pp;
  PirShape shape;
  bool have_shape = false;
  int n_hash = 0, n_shape = 0, n_coeffs = 0, n_buckets_checked = 0;
  Bytes hseed;
  std::size_t hbuckets = 0;
  while (std::getline(f, line)) {
    const auto t = split(line);
    if (t.empty()) continue;
    if (t[0] == "params") {
      pp.ring_dim = static_cast<std::uint32_t>(std::stoul(t[1]));
      pp.plain_modulus = std::stoull(t[2]);
      CHECK(pp.coeff_bits() == 19 && pp.plaintext_bytes() == 19456);
    } else if (t[0] == "hash") {
      hseed = from_hex(t[1]);
      hbuckets = std::stoull(t[2]);
    } else if (t[0] == "h") {
      const auto got = candidate_buckets(hseed, std::stoull(t[1]), hbuckets);
      CHECK(got.size() == 3);
      for (std::size_t i = 0; i < 3 && i < got.size(); ++i) CHECK(got[i] == std::stoull(t[2 + i]));
      ++n_hash;
    } else if (t[0] == "shape") {
      shape = make_shape(std::stoull(t[1]), static_cast<std::uint32_t>(std::stoul(t[2])),
                         std::stoull(t[3]), from_hex(t[4]), pp);
      have_shape = true;
      ++n_shape;
    } else if (t[0] == "f") {
      CHECK(have_shape);
      const std::vector<std::size_t> want = {shape.buckets,     shape.rows_per_pt,
                                             shape.bucket_rows, shape.pts_per_bucket,
                                             shape.r1,          shape.r2,
                                             shape.per_ct,      shape.n_cts,
                                             shape.levels,      shape.coeffs_per_bucket()};
      for (std::size_t i = 0; i < want.size(); ++i) CHECK(want[i] == std::stoull(t[1 + i]));
      std::size_t pos = 1 + want.size();
      CHECK(t[pos] == "|");
      std::vector<std::uint32_t> idx;
      for (++pos; t[pos] != "|"; ++pos)
        idx.push_back(static_cast<std::uint32_t>(std::stoul(t[pos])));
      CHECK(idx == shape.automorphism_indices());
      for (std::size_t c = 0; ++pos < t.size(); ++c)
        CHECK(shape.buckets_in_ct(c) == std::stoull(t[pos]));
    } else if (t[0] == "c") {
      const std::size_t b = std::stoull(t[1]);
      std::vector<std::uint64_t> rows;
      for (std::size_t i = 2; i < t.size(); ++i) rows.push_back(std::stoull(t[i]));
      CHECK(b < shape.contents.size() && shape.contents[b] == rows);
      ++n_buckets_checked;
    } else if (t[0] == "coeffs") {
      const std::size_t n = std::stoull(t[1]);
      Bytes data(n);
      for (std::size_t i = 0; i < n; ++i) data[i] = static_cast<std::uint8_t>((i * 37 + 5) % 256);
      const auto c = bytes_to_coeffs(data.data(), data.size(), pp);
      CHECK(c.size() == pp.ring_dim);
      std::int64_t sum = 0;
      std::uint64_t weighted = 0;
      for (std::size_t i = 0; i < c.size(); ++i) {
        sum += c[i];
        weighted += (i + 1) * static_cast<std::uint64_t>(c[i]);
      }
      CHECK(static_cast<std::uint64_t>(sum) == std::stoull(t[2]));
      CHECK(weighted == std::stoull(t[3]));
      for (std::size_t i = 0; i < 24; ++i) CHECK(c[i] == std::stoll(t[4 + i]));
      CHECK(t[28] == "|");
      for (std::size_t i = 0; i < 8; ++i) CHECK(c[c.size() - 8 + i] == std::stoll(t[29 + i]));
      ++n_coeffs;
    }
  }
  CHECK(n_hash == 27 && n_shape == 7 && n_coeffs == 6 && n_buckets_checked > 20);
}

void layout_errors() {
  const PirParams pp{8192, 786433};
  const Bytes seed(16, 1);
  auto throws = [](auto&& fn) {
    try {
      fn();
    } catch (const PirError&) {
      return true;
    }
    return false;
  };
  CHECK(throws([&] { make_shape(0, 1, 64, seed, pp); }));
  CHECK(throws([&] { make_shape(5, 0, 64, seed, pp); }));
  CHECK(throws([&] { make_shape(5, 1, 64, Bytes(15, 1), pp); }));
  CHECK(throws([&] { make_shape(5, 1, 20416, seed, pp); }));  // a row larger than one plaintext
  CHECK(throws([&] { candidate_buckets(seed, 0, 2); }));
  CHECK(n_buckets(1) == 3 && n_buckets(2) == 3 && n_buckets(3) == 5 && n_buckets(1000) == 1500);
}

}  // namespace

// The retrieval preset's numbers are available without OpenFHE and are the ones the golden file
// uses.
static void preset_numbers() {
  const PirParams pp = retrieval_params();
  CHECK(pp.ring_dim == 8192);
  CHECK(pp.coeff_bits() == 19 && pp.plaintext_bytes() == 19456);
}

int main() {
  preset_numbers();
  blake2b_vectors();
  layout_golden();
  layout_errors();
  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::puts("pir layout tests passed");
  return 0;
}
