// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Fuzz target: every gRPC request message of proto/ombra/v1/ombra.proto. The first byte picks the
// message type; the rest is parsed with ParseFromArray, as gRPC does with a request body. A parsed
// message must serialize and parse again to an equal message. A CreateCollectionRequest also goes
// through what the service runs on it: the conversion to a storage spec and validate_spec.
//
// Not covered: the other checks of service.cpp (id length, id count, chunk size, ...) are inline in
// the handlers and need a live server; limits.cpp and policy.cpp, the helpers they use, are covered
// here (validate_spec) and by the auth_header target.
#include <google/protobuf/stubs/common.h>
#include <google/protobuf/util/message_differencer.h>

#include <cstdint>
#include <memory>
#include <string>

#include "ombra/server/policy.hpp"
#include "ombra/storage/catalog.hpp"
#include "ombra/v1/ombra.pb.h"

namespace v1 = ombra::v1;

namespace {

template <class M>
void round_trip(const std::uint8_t* d, std::size_t n, M* out_msg = nullptr) {
  M m;
  if (!m.ParseFromArray(d, static_cast<int>(n))) return;
  std::string wire;
  if (!m.SerializeToString(&wire)) __builtin_trap();
  M again;
  if (!again.ParseFromString(wire)) __builtin_trap();
  if (!google::protobuf::util::MessageDifferencer::Equals(m, again)) __builtin_trap();
  if (out_msg) *out_msg = std::move(m);
}

void check_create(const v1::CreateCollectionRequest& req) {
  const auto& p = req.spec();
  ombra::storage::CollectionSpec s;
  s.api_version = p.api_version();
  s.name = p.name();
  s.preset = p.preset();
  s.dimension = p.dimension();
  s.security_level = static_cast<std::uint32_t>(p.security_level());
  for (const auto& f : p.filter_fields())
    s.filter_fields.push_back({f.field_id(), f.cardinality()});
  s.key_tag = p.key_tag();
  s.cluster_count = p.cluster_count();
  (void)ombra::server::validate_spec(s);
}

}  // namespace

extern "C" int LLVMFuzzerInitialize(int*, char***) {
  // A proto3 string with bad UTF-8 is logged by protobuf; keep the fuzzer output readable.
  google::protobuf::SetLogHandler(
      [](google::protobuf::LogLevel, const char*, int, const std::string&) {});
  return 0;
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* d, std::size_t n) {
  if (n < 1) return 0;
  const std::uint8_t* b = d + 1;
  const std::size_t m = n - 1;
  switch (d[0] % 14) {
    case 0: {
      v1::CreateCollectionRequest r;
      round_trip(b, m, &r);
      if (r.has_spec()) check_create(r);
      break;
    }
    case 1:
      round_trip<v1::GetCollectionRequest>(b, m);
      break;
    case 2:
      round_trip<v1::ListCollectionsRequest>(b, m);
      break;
    case 3:
      round_trip<v1::DropCollectionRequest>(b, m);
      break;
    case 4:
      round_trip<v1::UploadKeysChunk>(b, m);
      break;
    case 5:
      round_trip<v1::InsertBlock>(b, m);
      break;
    case 6:
      round_trip<v1::DeleteRequest>(b, m);
      break;
    case 7:
      round_trip<v1::SearchRequest>(b, m);
      break;
    case 8:
      round_trip<v1::ListBlocksRequest>(b, m);
      break;
    case 9:
      round_trip<v1::GetBlockRequest>(b, m);
      break;
    case 10:
      round_trip<v1::ReplaceBlockRequest>(b, m);
      break;
    case 11:
      round_trip<v1::PutBlobRequest>(b, m);
      break;
    case 12:
      round_trip<v1::GetBlobRequest>(b, m);
      break;
    default:
      round_trip<v1::DeleteBlobsRequest>(b, m);
      break;
  }
  return 0;
}
