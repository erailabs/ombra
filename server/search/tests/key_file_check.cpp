// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Test tool: checks one key file written by the real Python client against the structure template
// of a preset (server/search/src/context_preflight.hpp). Used by tests/test_openfhe_key_templates.py
// with synthetic keys.
//
//   search_key_file_check <preset> <evalmult|auto> <file>              accept or refuse (exit 0/1)
//   search_key_file_check <preset> <evalmult|auto> <file> flips <step>  also flip every <step>-th
//        structure byte (each change must be refused) and a few coefficient bytes (must be accepted)
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "context_preflight.hpp"
#include "ombra/search/session.hpp"

namespace det = ombra::search::detail;

int main(int argc, char** argv) {
  if (argc != 4 && argc != 6) {
    std::fprintf(stderr, "usage: %s preset evalmult|auto file [flips step]\n", argv[0]);
    return 2;
  }
  const std::string preset = argv[1];
  const bool is_auto = std::string(argv[2]) == "auto";
  const auto kind = is_auto ? det::KeyKind::Automorphism : det::KeyKind::EvalMult;
  std::ifstream in(argv[3], std::ios::binary);
  std::vector<std::uint8_t> file((std::istreambuf_iterator<char>(in)), {});
  try {
    det::require_key_file(preset, kind, file);
  } catch (const ombra::search::InvalidQuery& e) {
    std::printf("refused: %s\n", e.what());
    return 1;
  }
  if (argc == 4) {
    std::puts("accepted");
    return 0;
  }
  const std::size_t step = std::stoul(argv[5]);
  // The number of keys is the one whose file has this size.
  const std::size_t top = det::describe_key_file(preset, kind, 1).max_keys;
  det::KeyFileMap map;
  for (std::size_t n = 1; n <= top; ++n) {
    map = det::describe_key_file(preset, kind, n);
    if (map.size == file.size()) break;
  }
  if (map.size != file.size()) {
    std::puts("no key count matches the size");
    return 1;
  }
  std::vector<bool> is_free(file.size(), false);
  for (const auto& [a, b] : map.free_ranges)
    for (std::size_t i = a; i < b; ++i) is_free[i] = true;
  std::size_t flips = 0, wrongly_accepted = 0;
  for (std::size_t i = 0, k = 0; i < file.size(); ++i) {
    if (is_free[i] || k++ % step != 0) continue;
    file[i] ^= 0x01;
    try {
      det::require_key_file(preset, kind, file);
      ++wrongly_accepted;
      std::printf("flip at %zu accepted\n", i);
    } catch (const ombra::search::InvalidQuery&) {
    }
    file[i] ^= 0x01;
    ++flips;
  }
  std::size_t free_ok = 0;
  for (const auto& [a, b] : map.free_ranges) {
    for (const std::size_t i : {a, a + 3, (a + b) / 2, b - 1}) {
      file[i] ^= 0xFF;
      try {
        det::require_key_file(preset, kind, file);
        ++free_ok;
      } catch (const ombra::search::InvalidQuery& e) {
        std::printf("coefficient change at %zu refused: %s\n", i, e.what());
        return 1;
      }
      file[i] ^= 0xFF;
    }
  }
  std::printf("flips %zu wrongly_accepted %zu free_ok %zu\n", flips, wrongly_accepted, free_ok);
  return wrongly_accepted == 0 ? 0 : 1;
}
