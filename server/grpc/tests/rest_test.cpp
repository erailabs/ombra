// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// CPU unit test of the pure parts of the REST admin API: JSON reader, routes, create body, error
// mapping.
#include <cstdio>
#include <cstdlib>
#include <string>

#include "ombra/server/policy.hpp"
#include "ombra/server/rest_api.hpp"

#define CHECK(c)                                                                \
  do {                                                                          \
    if (!(c)) {                                                                 \
      std::fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #c); \
      std::exit(1);                                                             \
    }                                                                           \
  } while (0)

using namespace ombra::server;

static bool ok(const std::string& t) {
  Json j;
  std::string e;
  return parse_json(t, &j, &e);
}

static void test_json() {
  Json j;
  std::string e;
  CHECK(parse_json(R"({"a":1,"b":[true,null,"x\n\u00e9\ud83d\ude00"],"c":{"d":-5}})", &j, &e));
  CHECK(j.type == Json::Type::Object && j.find("a")->integer == 1 && j.find("a")->is_int);
  CHECK(j.find("b")->items[2].str == "x\n\xc3\xa9\xf0\x9f\x98\x80");
  CHECK(j.find("c")->find("d")->integer == -5 && !j.find("zz"));
  CHECK(ok("  [] ") && ok("0") && ok("1.5e3") && ok("\"\""));
  Json f;
  CHECK(parse_json("1.5", &f, &e) && !f.is_int);
  CHECK(parse_json("99999999999999999999", &f, &e) && !f.is_int);
  CHECK(!ok("") && !ok("{") && !ok("{\"a\":1,}") && !ok("[1,]") && !ok("{'a':1}") && !ok("01") &&
        !ok("1 2") && !ok("nul") && !ok("\"a\nb\"") && !ok("\"\\x\"") && !ok("\"\\u12\"") &&
        !ok("\"\\ud800\"") && !ok("\"\\u0000\"") && !ok("{\"a\":1,\"a\":2}") && !ok("-") &&
        !ok("1.") && !ok("[1 2]") && !ok("{\"a\" 1}") && !ok("{1:2}"));
  std::string deep(kMaxJsonDepth + 2, '[');
  deep += std::string(kMaxJsonDepth + 2, ']');
  CHECK(!ok(deep));
  CHECK(ok(std::string(kMaxJsonDepth, '[') + std::string(kMaxJsonDepth, ']')));
  std::string many = "[";
  for (std::size_t i = 0; i < kMaxJsonNodes + 5; ++i) many += i ? ",0" : "0";
  CHECK(!ok(many + "]"));
  CHECK(!ok(std::string(64 * 1024 + 1, ' ')));
  CHECK(json_string("a\"b\\c\n\x01") == "\"a\\\"b\\\\c\\n\\u0001\"");
}

static void test_routes() {
  CHECK(match_route("/healthz").kind == RouteKind::None);
  CHECK(match_route("/v1").kind == RouteKind::None);
  CHECK(match_route("/v1/").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/other").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections").kind == RouteKind::List);
  CHECK(match_route("/v1/collections/").kind == RouteKind::List);
  CHECK(match_route("/v1/collections?x=1").kind == RouteKind::List);
  const Route r = match_route("/v1/collections/docs-1?x=1");
  CHECK(r.kind == RouteKind::Collection && r.name == "docs-1");
  CHECK(match_route("/v1/collections/a%20b").name == "a b");
  CHECK(match_route("/v1/collections/a%2fb").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections/a/b").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections/a%zz").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections/a%2").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections/a%").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections/a%00").kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collections/" + std::string(128, 'a')).kind == RouteKind::Collection);
  CHECK(match_route("/v1/collections/" + std::string(129, 'a')).kind == RouteKind::Unknown);
  CHECK(match_route("/v1/collectionsx").kind == RouteKind::Unknown);
}

static void test_create_body() {
  ombra::storage::CollectionSpec s;
  std::string e;
  const std::string good =
      R"({"name":"docs","preset":"m0_depth2","dimension":16,"security_level":"decoy",)"
      R"("key_tag":"t","cluster_count":4,"filter_fields":[{"field_id":0,"cardinality":3}]})";
  CHECK(parse_create_body(good, &s, &e));
  CHECK(s.name == "docs" && s.preset == "m0_depth2" && s.dimension == 16 &&
        s.security_level == 2 && s.key_tag == "t" && s.cluster_count == 4 && s.api_version == 1 &&
        s.filter_fields.size() == 1 && s.filter_fields[0].cardinality == 3);
  const auto bad = [&](const std::string& body) {
    ombra::storage::CollectionSpec x;
    std::string why;
    return !parse_create_body(body, &x, &why) && !why.empty();
  };
  CHECK(bad("not json") && bad("[]") && bad("{}"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":1,"key_tag":"t","cluster_count":1})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":1,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"low"})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":-1,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast"})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":1.5,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast"})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":4294967296,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast"})"));
  CHECK(bad(R"({"name":5,"preset":"p","dimension":1,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast"})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":1,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast","extra":1})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":1,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast","filter_fields":[{"field_id":0}]})"));
  CHECK(bad(R"({"name":"d","preset":"p","dimension":1,"key_tag":"t","cluster_count":1,)"
            R"("security_level":"fast","filter_fields":{}})"));
  CHECK(std::string(security_level_name(1)) == "fast" && std::string(security_level_name(3)) == "strict");
}

static void test_errors() {
  CHECK(err_http_status(Err::CollectionNotFound) == 404);
  CHECK(err_http_status(Err::CollectionExists) == 409);
  CHECK(err_http_status(Err::InvalidArgument) == 400);
  CHECK(err_http_status(Err::InvalidFilterLayout) == 400);
  CHECK(err_http_status(Err::SizeLimit) == 400);
  CHECK(err_http_status(Err::StorageFull) == 503);
  CHECK(err_http_status(Err::Internal) == 500);
  CHECK(std::string(err_name(Err::CollectionNotFound)) == "COLLECTION_NOT_FOUND");
  CHECK(std::string(err_name(Err::InvalidFilterLayout)) == "INVALID_FILTER_LAYOUT");
  CHECK(rest_error_body("X", "a\"b", "r1") ==
        "{\"error\":{\"code\":\"X\",\"message\":\"a\\\"b\",\"request_id\":\"r1\"}}");
}

int main() {
  test_json();
  test_routes();
  test_create_body();
  test_errors();
  return 0;
}
