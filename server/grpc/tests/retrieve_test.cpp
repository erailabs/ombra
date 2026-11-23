// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// RetrieveRecords request validation: the start message, the stream order, the limits.
#include "ombra/server/retrieve.hpp"

#include <cstdio>
#include <cstdlib>
#include <string>

namespace v1 = ombra::v1;
using namespace ombra::server;

#define CHECK(c)                                                                \
  do {                                                                          \
    if (!(c)) {                                                                 \
      std::fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #c); \
      std::exit(1);                                                             \
    }                                                                           \
  } while (0)

static v1::RetrieveRecordsStart good() {
  v1::RetrieveRecordsStart s;
  s.set_collection("docs");
  s.set_key_tag("k");
  s.set_pir_key_tag("p");
  s.set_all_clusters(true);
  s.set_m(10);
  s.set_hash_seed(std::string(16, 'x'));
  s.set_n_queries(2);
  return s;
}

static v1::RetrieveRecordsRequest start_msg(const v1::RetrieveRecordsStart& s, int ver = 1) {
  v1::RetrieveRecordsRequest r;
  r.set_api_version(ver);
  *r.mutable_start() = s;
  return r;
}

static v1::RetrieveRecordsRequest query_msg(const std::string& q = "q") {
  v1::RetrieveRecordsRequest r;
  r.set_api_version(1);
  r.set_query(q);
  return r;
}

int main() {
  CHECK(validate_retrieve_start(good()).ok());
  {
    auto s = good();
    s.set_all_clusters(false);
    CHECK(validate_retrieve_start(s).code == Err::InvalidArgument);  // no scope
    s.add_cluster_ids(1);
    s.add_cluster_ids(2);
    CHECK(validate_retrieve_start(s, 4).ok());
    CHECK(validate_retrieve_start(s, 2).code == Err::InvalidArgument);  // 2 is the center id
    s.set_all_clusters(true);
    CHECK(validate_retrieve_start(s).code == Err::InvalidArgument);  // both scopes
  }
  {
    auto s = good();
    s.set_all_clusters(false);
    s.add_cluster_ids(1);
    s.add_cluster_ids(1);
    CHECK(validate_retrieve_start(s).code == Err::InvalidArgument);  // duplicate
  }
  for (auto edit : {+[](v1::RetrieveRecordsStart& s) { s.clear_collection(); },
                    +[](v1::RetrieveRecordsStart& s) { s.clear_key_tag(); },
                    +[](v1::RetrieveRecordsStart& s) { s.clear_pir_key_tag(); },
                    +[](v1::RetrieveRecordsStart& s) { s.set_m(0); },
                    +[](v1::RetrieveRecordsStart& s) { s.set_hash_seed("short"); },
                    +[](v1::RetrieveRecordsStart& s) { s.set_hash_seed(std::string(17, 'x')); }}) {
    auto s = good();
    edit(s);
    CHECK(validate_retrieve_start(s).code == Err::InvalidArgument);
  }
  {
    auto s = good();
    s.set_m(kMaxRetrieveM);
    CHECK(validate_retrieve_start(s).ok());
    s.set_m(kMaxRetrieveM + 1);
    CHECK(validate_retrieve_start(s).code == Err::SizeLimit);
    s = good();
    s.set_n_queries(kMaxRetrieveQueries + 1);
    CHECK(validate_retrieve_start(s).code == Err::SizeLimit);
  }
  {  // n_queries 0 is valid: the shape decides
    auto s = good();
    s.set_n_queries(0);
    CHECK(validate_retrieve_start(s).ok());
    RetrieveValidator v;
    CHECK(v.accept(start_msg(s)).ok());
    CHECK(v.finish().code == Err::BadSequence);  // the count is not known yet
    CHECK(v.expect_queries(0).code == Err::InvalidArgument);
    CHECK(v.expect_queries(3).ok());
    CHECK(v.accept(query_msg()).ok() && v.accept(query_msg()).ok());
    CHECK(v.finish().code == Err::BadSequence);  // short
    CHECK(v.accept(query_msg()).ok());
    CHECK(v.finish().ok());
    CHECK(v.accept(query_msg()).code == Err::BadSequence);  // an extra one
  }
  {  // a non-zero n_queries must equal the shape's
    RetrieveValidator v;
    CHECK(v.expect_queries(2).code == Err::BadSequence);  // no start yet
    CHECK(v.accept(start_msg(good())).ok());              // n_queries 2
    CHECK(v.expect_queries(3).code == Err::InvalidArgument);
    CHECK(v.expect_queries(2).ok());
    CHECK(v.accept(query_msg()).ok() && v.accept(query_msg()).ok() && v.finish().ok());
  }
  {  // a whole good stream
    RetrieveValidator v;
    CHECK(v.accept(start_msg(good())).ok());
    CHECK(v.accept(query_msg()).ok() && v.accept(query_msg()).ok());
    CHECK(v.finish().ok());
    CHECK(v.accept(query_msg()).code == Err::BadSequence);  // one too many
  }
  {
    RetrieveValidator v;
    CHECK(v.accept(query_msg()).code == Err::BadSequence);  // query first
  }
  {
    RetrieveValidator v;
    CHECK(v.accept(start_msg(good(), 2)).code == Err::UnsupportedVersion);
    CHECK(v.accept(start_msg(good())).ok());
    CHECK(v.accept(start_msg(good())).code == Err::BadSequence);  // second start
    CHECK(v.finish().code == Err::BadSequence);                   // no queries yet
    CHECK(v.accept(query_msg("")).code == Err::InvalidArgument);
    CHECK(v.accept(query_msg(std::string(kMaxChunkBytes + 1, 'q'))).code == Err::SizeLimit);
    v1::RetrieveRecordsRequest empty;
    empty.set_api_version(1);
    CHECK(v.accept(empty).code == Err::InvalidArgument);
  }
  CHECK(RetrieveValidator().finish().code == Err::BadSequence);
  std::puts("retrieve_test ok");
  return 0;
}
