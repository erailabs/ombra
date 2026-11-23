// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Private-retrieval key files: stored per collection next to the CKKS keys, checked against the
// retrieval preset (not the collection's), replaced as a set, and kept across a reopen. Synthetic
// bytes: the store checks the envelope header only.
#include <filesystem>

#include "ombra/storage/store.hpp"
#include "test_util.hpp"

using namespace ombra::storage;
using namespace ombra::storage::testing;
namespace fs = std::filesystem;
using K = ombra::core::EnvelopeKind;

namespace {

std::vector<std::uint8_t> env(K k, const std::string& preset, std::uint8_t fill) {
  return ombra::core::write_envelope({k, preset, kOfhe, std::vector<std::uint8_t>(48, fill)});
}

using Files = std::vector<std::pair<std::string, std::vector<std::uint8_t>>>;

Files pir_set(std::uint8_t fill, const std::string& preset = kPirPreset) {
  return {
      {kKeyPirContext, env(K::Context, preset, fill)},
      {kKeyPirEvalMult, env(K::PublicEvalKey, preset, static_cast<std::uint8_t>(fill + 1))},
      {kKeyPirAutomorphism, env(K::PublicEvalKey, preset, static_cast<std::uint8_t>(fill + 2))}};
}

std::size_t key_file_count(const std::string& root) {
  std::size_t n = 0;
  for (auto& e : fs::directory_iterator(root + "/keys")) {
    (void)e;
    ++n;
  }
  return n;
}

}  // namespace

int main() {
  TempDir d;
  Store s(d.path, kOfhe);
  s.create_collection(spec());

  // Put and read back; the collection's CKKS preset is not the retrieval preset.
  CHECK(std::string(kPirPreset) != kPreset);
  const auto a = pir_set(10);
  s.put_key_files("docs", a);
  for (const auto& [k, bytes] : a) {
    std::vector<std::uint8_t> got;
    CHECK(s.get_key_file("docs", k, &got));
    CHECK(got == bytes);
  }
  CHECK(key_file_count(d.path) == 3);

  // CKKS keys live beside them; replacing one set leaves the other alone.
  s.put_key_files("docs", {{kKeyContext, env(K::Context, kPreset, 1)},
                           {kKeyEvalMult, env(K::PublicEvalKey, kPreset, 2)},
                           {kKeyAutomorphism, env(K::PublicEvalKey, kPreset, 3)}});
  CHECK(key_file_count(d.path) == 6);
  const auto b = pir_set(50);
  s.put_key_files("docs", b);  // replaced: the old three files are deleted
  CHECK(key_file_count(d.path) == 6);
  for (const auto& [k, bytes] : b) {
    std::vector<std::uint8_t> got;
    CHECK(s.get_key_file("docs", k, &got) && got == bytes);
  }
  CHECK(s.get_key_file("docs", kKeyContext, nullptr));

  // Survives a reopen.
  {
    Store again(d.path, kOfhe);
    std::vector<std::uint8_t> got;
    CHECK(again.get_key_file("docs", kKeyPirEvalMult, &got) && got == b[1].second);
  }

  // Wrong preset, the collection's CKKS preset, wrong envelope kind, wrong OpenFHE version, an
  // unknown kind name: refused, and nothing changes.
  CHECK_THROWS(s.put_key_files("docs", pir_set(70, "pir_bfv_other")));
  CHECK_THROWS(s.put_key_files("docs", pir_set(70, kPreset)));
  CHECK_THROWS(s.put_key_file("docs", kKeyPirContext, env(K::PublicEvalKey, kPirPreset, 1)));
  CHECK_THROWS(s.put_key_file("docs", kKeyPirEvalMult, env(K::Context, kPirPreset, 1)));
  CHECK_THROWS(
      s.put_key_file("docs", kKeyPirContext,
                     ombra::core::write_envelope({K::Context, kPirPreset, "9.9.9", {1, 2, 3}})));
  CHECK_THROWS(s.put_key_file("docs", kKeyContext, env(K::Context, kPirPreset, 1)));
  CHECK_THROWS(s.put_key_file("docs", "pir_unknown", env(K::Context, kPirPreset, 1)));
  CHECK(key_file_count(d.path) == 6);
  std::vector<std::uint8_t> got;
  CHECK(s.get_key_file("docs", kKeyPirContext, &got) && got == b[0].second);
  return 0;
}
