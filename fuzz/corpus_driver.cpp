// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Runs a libFuzzer entry point over every file of the given corpus directories, without libFuzzer,
// so the normal test run (ctest) exercises the entry points. Usage: driver <dir>... ; exits 1 if a
// directory has no file. A crash or sanitizer report fails the process, as in libFuzzer.
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* d, std::size_t n);
extern "C" int LLVMFuzzerInitialize(int*, char***) __attribute__((weak));

int main(int argc, char** argv) {
  if (LLVMFuzzerInitialize) LLVMFuzzerInitialize(&argc, &argv);
  std::size_t files = 0;
  for (int i = 1; i < argc; ++i) {
    std::size_t in_dir = 0;
    for (const auto& e : std::filesystem::directory_iterator(argv[i])) {
      if (!e.is_regular_file()) continue;
      std::ifstream f(e.path(), std::ios::binary);
      const std::vector<std::uint8_t> b{std::istreambuf_iterator<char>(f),
                                        std::istreambuf_iterator<char>()};
      LLVMFuzzerTestOneInput(b.data(), b.size());
      ++in_dir;
    }
    if (in_dir == 0) {
      std::fprintf(stderr, "empty corpus directory %s\n", argv[i]);
      return 1;
    }
    files += in_dir;
  }
  std::printf("corpus ok: %zu inputs\n", files);
  return 0;
}
