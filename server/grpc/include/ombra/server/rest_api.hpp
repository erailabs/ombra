// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Pure parts of the REST admin API (/v1/collections): a small strict JSON reader, JSON output
// helpers, route matching and the body of "create collection". No network, no gRPC, so it is unit
// tested on the CPU. Nothing here logs.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "ombra/storage/catalog.hpp"

namespace ombra::server {

// ---- JSON -------------------------------------------------------------------------------------
struct Json {
  enum class Type { Null, Bool, Number, String, Array, Object };
  Type type = Type::Null;
  bool boolean = false;
  bool is_int = false;  // Number written as an integer (no fraction or exponent) that fits int64
  std::int64_t integer = 0;
  std::string str;
  std::vector<Json> items;                           // Array
  std::vector<std::pair<std::string, Json>> fields;  // Object, in order
  const Json* find(std::string_view key) const;      // first field with this name, or nullptr
};

inline constexpr int kMaxJsonDepth = 8;
inline constexpr std::size_t kMaxJsonNodes = 2000;

// Parses one complete JSON document (RFC 8259) of at most 64 KiB. False on any error (the reason
// is a fixed text), nesting above kMaxJsonDepth or more than kMaxJsonNodes values. Strings must be free of
// control characters; \u escapes become UTF-8. Duplicate keys are refused.
bool parse_json(std::string_view text, Json* out, std::string* error);

// The text as a JSON string literal with quotes.
std::string json_string(std::string_view s);

// ---- Routes -----------------------------------------------------------------------------------
enum class RouteKind {
  None,         // not under /v1/
  Unknown,      // under /v1/ but not a route
  List,         // /v1/collections
  Collection,   // /v1/collections/{name}
};
struct Route {
  RouteKind kind = RouteKind::None;
  std::string name;  // percent-decoded, for Collection
};
// `path` is the request target (a query string is ignored). A name that is empty, longer than the
// collection name limit, has a bad percent escape, or holds a control character or '/' is Unknown.
Route match_route(std::string_view path);

// ---- Create body ------------------------------------------------------------------------------
// {"name", "preset", "dimension", "security_level": "fast"|"decoy"|"strict", "key_tag",
//  "cluster_count", "filter_fields": [{"field_id", "cardinality"}...] (optional),
//  "api_version" (optional, 1)}. Unknown fields are refused. Fills `spec` (validate_spec still has
// to run) or returns false with a fixed error text.
bool parse_create_body(std::string_view body, storage::CollectionSpec* spec, std::string* error);

// {"error":{"code":"...","message":"...","request_id":"..."}}. `code` is a proto error name
// (policy.hpp err_name), a gRPC status name (UNAUTHENTICATED, PERMISSION_DENIED, UNAVAILABLE) or one
// of NOT_FOUND, METHOD_NOT_ALLOWED, PAYLOAD_TOO_LARGE. `message` is a fixed text, never user data.
std::string rest_error_body(std::string_view code, std::string_view message,
                            std::string_view request_id);

const char* security_level_name(std::uint32_t level);  // "fast", "decoy", "strict", "unspecified"

}  // namespace ombra::server
