// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/server/retrieve.hpp"

#include <set>

namespace ombra::server {

namespace v1 = ombra::v1;

static_assert(static_cast<std::uint32_t>(v1::LIMIT_MAX_RETRIEVE_M) == kMaxRetrieveM);
static_assert(static_cast<std::uint32_t>(v1::LIMIT_MAX_RETRIEVE_QUERIES) == kMaxRetrieveQueries);

namespace {
Status bad(Err c, const char* m) { return Status{c, m}; }
}  // namespace

Status validate_retrieve_start(const v1::RetrieveRecordsStart& s, std::uint32_t cluster_count) {
  if (s.collection().empty() || s.collection().size() > kMaxNameBytes)
    return bad(Err::InvalidArgument, "invalid collection name");
  if (s.key_tag().empty() || s.key_tag().size() > kMaxKeyTagBytes)
    return bad(Err::InvalidArgument, "invalid key tag");
  if (s.pir_key_tag().empty() || s.pir_key_tag().size() > kMaxKeyTagBytes)
    return bad(Err::InvalidArgument, "invalid retrieval key tag");
  if (s.all_clusters() == (s.cluster_ids_size() > 0))
    return bad(Err::InvalidArgument, "give either a cluster list or all_clusters");
  if (static_cast<std::size_t>(s.cluster_ids_size()) > kMaxClustersPerRound)
    return bad(Err::SizeLimit, "too many clusters");
  std::set<std::uint32_t> seen;
  for (const auto c : s.cluster_ids()) {
    if (!seen.insert(c).second) return bad(Err::InvalidArgument, "duplicate cluster id");
    if (cluster_count != 0 && c >= cluster_count)
      return bad(Err::InvalidArgument, "cluster id out of range");
  }
  if (s.m() == 0) return bad(Err::InvalidArgument, "m must be positive");
  if (s.m() > kMaxRetrieveM) return bad(Err::SizeLimit, "m is above the limit");
  if (s.hash_seed().size() != kPirSeedBytes)
    return bad(Err::InvalidArgument, "hash_seed must be 16 bytes");
  if (s.n_queries() > kMaxRetrieveQueries) return bad(Err::SizeLimit, "too many queries");
  return {};
}

Status RetrieveValidator::accept(const v1::RetrieveRecordsRequest& m) {
  if (m.api_version() != kApiVersion)
    return bad(Err::UnsupportedVersion, "unsupported api_version");
  switch (m.part_case()) {
    case v1::RetrieveRecordsRequest::kStart: {
      if (started_) return bad(Err::BadSequence, "second start");
      if (auto st = validate_retrieve_start(m.start(), cluster_count_); !st.ok()) return st;
      start_ = m.start();
      started_ = true;
      return {};
    }
    case v1::RetrieveRecordsRequest::kQuery:
      if (!started_) return bad(Err::BadSequence, "a query before the start");
      if (queries_ >= (expected_ != 0 ? expected_ : start_.n_queries()))
        return bad(Err::BadSequence, "more queries than the call needs");
      if (m.query().empty()) return bad(Err::InvalidArgument, "empty query");
      if (m.query().size() > kMaxChunkBytes) return bad(Err::SizeLimit, "query is too large");
      ++queries_;
      return {};
    case v1::RetrieveRecordsRequest::PART_NOT_SET:
      break;
  }
  return bad(Err::InvalidArgument, "empty message");
}

Status RetrieveValidator::finish() const {
  if (!started_) return bad(Err::BadSequence, "no start");
  const std::uint32_t want = expected_ != 0 ? expected_ : start_.n_queries();
  if (want == 0) return bad(Err::BadSequence, "the number of queries is not known yet");
  if (queries_ != want) return bad(Err::BadSequence, "fewer queries than the call needs");
  return {};
}

Status RetrieveValidator::expect_queries(std::uint32_t n_cts) {
  if (!started_) return bad(Err::BadSequence, "no start");
  if (n_cts == 0) return bad(Err::InvalidArgument, "the call needs no query");
  if (start_.n_queries() != 0 && start_.n_queries() != n_cts)
    return bad(Err::InvalidArgument, "n_queries differs from the number of queries the call needs");
  expected_ = n_cts;
  return {};
}

}  // namespace ombra::server
