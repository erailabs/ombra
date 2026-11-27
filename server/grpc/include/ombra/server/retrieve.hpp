// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Validation of the RetrieveRecords request stream, without a store or an engine: the checks that
// need only the messages (and, optionally, the collection's cluster count). Plain C++ over the
// generated messages, so it can be tested and fuzzed on the CPU.
#pragma once

#include <cstdint>
#include <optional>

#include "ombra/server/policy.hpp"
#include "ombra/v1/ombra.pb.h"

namespace ombra::server {

// The refusal of a scope without records. The router matches it to leave out a node whose part of
// the scope is empty, so the node and the router share this text.
inline constexpr const char* kEmptyScopeMessage = "the scope has no records";

// A RetrieveDocuments request as the RetrieveRecords validator takes it: the same checks apply to
// the start (the document class is checked by the caller) and to the queries.
v1::RetrieveRecordsRequest as_records_request(const v1::RetrieveDocumentsRequest& d);

// Checks one RetrieveRecordsStart: names, scope (exactly one of a cluster list or all clusters;
// distinct ids, each below cluster_count when it is non-zero), m, the 16-byte seed, the query
// count.
Status validate_retrieve_start(const v1::RetrieveRecordsStart& s, std::uint32_t cluster_count = 0);

// Feeds the messages of one call in order. accept() checks the api_version, that the first message
// is the start and the rest are queries, that each query is non-empty and at most kMaxChunkBytes,
// and that no more queries arrive than the call needs. How many it needs is known once the server
// has built the shape: expect_queries(shape.n_cts) (a non-zero n_queries of the start must equal
// it; 0 means "as many as the shape needs"). Before that, a start with a non-zero n_queries bounds
// the queries, and one with 0 allows none to be counted complete. finish() checks that all arrived.
class RetrieveValidator {
 public:
  explicit RetrieveValidator(std::uint32_t cluster_count = 0) : cluster_count_(cluster_count) {}
  Status accept(const v1::RetrieveRecordsRequest& m);
  Status finish() const;
  // The queries the shape needs. ERROR_INVALID_ARGUMENT if the start named another non-zero count.
  Status expect_queries(std::uint32_t n_cts);
  const v1::RetrieveRecordsStart* start() const { return started_ ? &start_ : nullptr; }

 private:
  std::uint32_t cluster_count_;
  bool started_ = false;
  v1::RetrieveRecordsStart start_;
  std::uint32_t queries_ = 0;
  std::uint32_t expected_ = 0;  // 0: not known yet
};

}  // namespace ombra::server
