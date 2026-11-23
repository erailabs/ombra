// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Fuzz target: RetrieveRecords request validation (server/grpc/src/retrieve.cpp). Input: one byte
// (the collection's cluster count, 0 = unchecked), then messages, each a u16 little-endian length
// and that many bytes of a serialized RetrieveRecordsRequest. Each message that parses is fed to
// the stream validator in order, as the server does; the first refusal ends the stream. A start
// that the validator accepts must satisfy the limits it promises.
#include <google/protobuf/stubs/common.h>

#include <cstdint>
#include <cstdlib>

#include "ombra/server/retrieve.hpp"

namespace v1 = ombra::v1;
namespace srv = ombra::server;

extern "C" int LLVMFuzzerInitialize(int*, char***) {
  google::protobuf::SetLogHandler(
      [](google::protobuf::LogLevel, const char*, int, const std::string&) {});
  return 0;
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* d, std::size_t n) {
  if (n < 1) return 0;
  srv::RetrieveValidator check(d[0]);
  std::size_t at = 1;
  while (at + 2 <= n) {
    const std::size_t len = d[at] | (static_cast<std::size_t>(d[at + 1]) << 8);
    at += 2;
    if (at + len > n) break;
    v1::RetrieveRecordsRequest m;
    const bool parsed = m.ParseFromArray(d + at, static_cast<int>(len));
    at += len;
    if (!parsed) break;
    if (!check.accept(m).ok()) return 0;
  }
  if (check.finish().ok()) {
    const auto* s = check.start();
    if (s == nullptr || s->m() == 0 || s->m() > srv::kMaxRetrieveM ||
        s->hash_seed().size() != srv::kPirSeedBytes || s->n_queries() == 0 ||
        s->n_queries() > srv::kMaxRetrieveQueries)
      std::abort();
  }
  return 0;
}
