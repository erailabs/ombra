// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/server/rest_api.hpp"

#include <cstdio>

#include "ombra/server/policy.hpp"

namespace ombra::server {

const Json* Json::find(std::string_view key) const {
  for (const auto& f : fields)
    if (f.first == key) return &f.second;
  return nullptr;
}

namespace {

struct Reader {
  std::string_view t;
  std::size_t i = 0;
  std::size_t nodes = 0;
  const char* err = nullptr;

  bool fail(const char* m) {
    if (!err) err = m;
    return false;
  }
  void ws() {
    while (i < t.size() && (t[i] == ' ' || t[i] == '\t' || t[i] == '\n' || t[i] == '\r')) ++i;
  }
  static void put_utf8(std::string* s, std::uint32_t c) {
    if (c < 0x80) {
      s->push_back(static_cast<char>(c));
    } else if (c < 0x800) {
      s->push_back(static_cast<char>(0xC0 | (c >> 6)));
      s->push_back(static_cast<char>(0x80 | (c & 0x3F)));
    } else if (c < 0x10000) {
      s->push_back(static_cast<char>(0xE0 | (c >> 12)));
      s->push_back(static_cast<char>(0x80 | ((c >> 6) & 0x3F)));
      s->push_back(static_cast<char>(0x80 | (c & 0x3F)));
    } else {
      s->push_back(static_cast<char>(0xF0 | (c >> 18)));
      s->push_back(static_cast<char>(0x80 | ((c >> 12) & 0x3F)));
      s->push_back(static_cast<char>(0x80 | ((c >> 6) & 0x3F)));
      s->push_back(static_cast<char>(0x80 | (c & 0x3F)));
    }
  }
  bool hex4(std::uint32_t* out) {
    if (i + 4 > t.size()) return fail("bad \\u escape");
    std::uint32_t v = 0;
    for (int k = 0; k < 4; ++k) {
      const char c = t[i + static_cast<std::size_t>(k)];
      v <<= 4;
      if (c >= '0' && c <= '9')
        v |= static_cast<std::uint32_t>(c - '0');
      else if (c >= 'a' && c <= 'f')
        v |= static_cast<std::uint32_t>(c - 'a' + 10);
      else if (c >= 'A' && c <= 'F')
        v |= static_cast<std::uint32_t>(c - 'A' + 10);
      else
        return fail("bad \\u escape");
    }
    i += 4;
    *out = v;
    return true;
  }
  bool string(std::string* out) {
    ++i;  // opening quote
    out->clear();
    while (i < t.size()) {
      const unsigned char c = static_cast<unsigned char>(t[i++]);
      if (c == '"') return true;
      if (c < 0x20) return fail("control character in a string");
      if (c != '\\') {
        out->push_back(static_cast<char>(c));
        continue;
      }
      if (i >= t.size()) break;
      const char e = t[i++];
      switch (e) {
        case '"': out->push_back('"'); break;
        case '\\': out->push_back('\\'); break;
        case '/': out->push_back('/'); break;
        case 'b': out->push_back('\b'); break;
        case 'f': out->push_back('\f'); break;
        case 'n': out->push_back('\n'); break;
        case 'r': out->push_back('\r'); break;
        case 't': out->push_back('\t'); break;
        case 'u': {
          std::uint32_t u = 0;
          if (!hex4(&u)) return false;
          if (u >= 0xD800 && u < 0xDC00) {
            std::uint32_t lo = 0;
            if (i + 2 > t.size() || t[i] != '\\' || t[i + 1] != 'u') return fail("bad surrogate");
            i += 2;
            if (!hex4(&lo)) return false;
            if (lo < 0xDC00 || lo > 0xDFFF) return fail("bad surrogate");
            u = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00);
          } else if (u >= 0xDC00 && u <= 0xDFFF) {
            return fail("bad surrogate");
          }
          if (u == 0) return fail("NUL in a string");
          put_utf8(out, u);
          break;
        }
        default: return fail("bad escape");
      }
    }
    return fail("unterminated string");
  }
  bool number(Json* out) {
    const std::size_t start = i;
    bool integral = true;
    if (i < t.size() && t[i] == '-') ++i;
    if (i >= t.size()) return fail("bad number");
    if (t[i] == '0') {
      ++i;
    } else if (t[i] >= '1' && t[i] <= '9') {
      while (i < t.size() && t[i] >= '0' && t[i] <= '9') ++i;
    } else {
      return fail("bad number");
    }
    if (i < t.size() && t[i] == '.') {
      integral = false;
      ++i;
      if (i >= t.size() || t[i] < '0' || t[i] > '9') return fail("bad number");
      while (i < t.size() && t[i] >= '0' && t[i] <= '9') ++i;
    }
    if (i < t.size() && (t[i] == 'e' || t[i] == 'E')) {
      integral = false;
      ++i;
      if (i < t.size() && (t[i] == '+' || t[i] == '-')) ++i;
      if (i >= t.size() || t[i] < '0' || t[i] > '9') return fail("bad number");
      while (i < t.size() && t[i] >= '0' && t[i] <= '9') ++i;
    }
    out->type = Json::Type::Number;
    const std::string_view lit = t.substr(start, i - start);
    if (integral && lit.size() <= 18) {  // fits int64 for sure
      std::int64_t v = 0;
      std::size_t k = lit[0] == '-' ? 1 : 0;
      for (; k < lit.size(); ++k) v = v * 10 + (lit[k] - '0');
      out->is_int = true;
      out->integer = lit[0] == '-' ? -v : v;
    }
    return true;
  }
  bool literal(std::string_view w) {
    if (t.substr(i, w.size()) != w) return fail("bad literal");
    i += w.size();
    return true;
  }
  bool value(Json* out, int depth) {
    if (++nodes > kMaxJsonNodes) return fail("too many values");
    if (depth > kMaxJsonDepth) return fail("nested too deeply");
    ws();
    if (i >= t.size()) return fail("unexpected end");
    const char c = t[i];
    if (c == '{') {
      out->type = Json::Type::Object;
      ++i;
      ws();
      if (i < t.size() && t[i] == '}') return ++i, true;
      for (;;) {
        ws();
        if (i >= t.size() || t[i] != '"') return fail("expected a field name");
        std::string key;
        if (!string(&key)) return false;
        if (out->find(key)) return fail("duplicate field");
        ws();
        if (i >= t.size() || t[i] != ':') return fail("expected ':'");
        ++i;
        Json v;
        if (!value(&v, depth + 1)) return false;
        out->fields.emplace_back(std::move(key), std::move(v));
        ws();
        if (i < t.size() && t[i] == ',') {
          ++i;
          continue;
        }
        if (i < t.size() && t[i] == '}') return ++i, true;
        return fail("expected ',' or '}'");
      }
    }
    if (c == '[') {
      out->type = Json::Type::Array;
      ++i;
      ws();
      if (i < t.size() && t[i] == ']') return ++i, true;
      for (;;) {
        Json v;
        if (!value(&v, depth + 1)) return false;
        out->items.push_back(std::move(v));
        ws();
        if (i < t.size() && t[i] == ',') {
          ++i;
          continue;
        }
        if (i < t.size() && t[i] == ']') return ++i, true;
        return fail("expected ',' or ']'");
      }
    }
    if (c == '"') {
      out->type = Json::Type::String;
      return string(&out->str);
    }
    if (c == 't') return out->type = Json::Type::Bool, out->boolean = true, literal("true");
    if (c == 'f') return out->type = Json::Type::Bool, literal("false");
    if (c == 'n') return out->type = Json::Type::Null, literal("null");
    return number(out);
  }
};

int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool get_uint32(const Json& j, std::uint32_t* out) {
  if (j.type != Json::Type::Number || !j.is_int || j.integer < 0 || j.integer > 0xFFFFFFFFll)
    return false;
  *out = static_cast<std::uint32_t>(j.integer);
  return true;
}

}  // namespace

bool parse_json(std::string_view text, Json* out, std::string* error) {
  if (text.size() > 64 * 1024) {
    if (error) *error = "the JSON text is too large";
    return false;
  }
  Reader r{text};
  Json v;
  const bool ok = r.value(&v, 0) && (r.ws(), r.i == text.size() || r.fail("trailing characters"));
  if (!ok) {
    if (error) *error = r.err ? r.err : "invalid JSON";
    return false;
  }
  *out = std::move(v);
  return true;
}

std::string json_string(std::string_view s) {
  std::string o = "\"";
  for (const unsigned char c : s) {
    switch (c) {
      case '"': o += "\\\""; break;
      case '\\': o += "\\\\"; break;
      case '\n': o += "\\n"; break;
      case '\r': o += "\\r"; break;
      case '\t': o += "\\t"; break;
      default:
        if (c < 0x20) {
          char b[8];
          std::snprintf(b, sizeof b, "\\u%04x", c);
          o += b;
        } else {
          o.push_back(static_cast<char>(c));
        }
    }
  }
  return o + "\"";
}

Route match_route(std::string_view path) {
  const std::size_t q = path.find('?');
  if (q != std::string_view::npos) path = path.substr(0, q);
  constexpr std::string_view kBase = "/v1/";
  if (path.substr(0, kBase.size()) != kBase) return {};
  Route r;
  r.kind = RouteKind::Unknown;
  path.remove_prefix(kBase.size());
  constexpr std::string_view kColl = "collections";
  if (path == kColl || path == std::string(kColl) + "/") {
    r.kind = RouteKind::List;
    return r;
  }
  if (path.substr(0, kColl.size() + 1) != std::string(kColl) + "/") return r;
  path.remove_prefix(kColl.size() + 1);
  std::string name;
  for (std::size_t i = 0; i < path.size(); ++i) {
    char c = path[i];
    if (c == '%') {
      if (i + 2 >= path.size()) return r;
      const int h = hexval(path[i + 1]), l = hexval(path[i + 2]);
      if (h < 0 || l < 0) return r;
      c = static_cast<char>(h * 16 + l);
      i += 2;
    }
    if (c == '/' || static_cast<unsigned char>(c) < 0x20 || c == 0x7f) return r;
    name.push_back(c);
  }
  if (name.empty() || name.size() > kMaxNameBytes) return r;
  r.kind = RouteKind::Collection;
  r.name = std::move(name);
  return r;
}

std::string rest_error_body(std::string_view code, std::string_view message,
                            std::string_view request_id) {
  return "{\"error\":{\"code\":" + json_string(code) + ",\"message\":" + json_string(message) +
         ",\"request_id\":" + json_string(request_id) + "}}";
}

const char* security_level_name(std::uint32_t level) {
  switch (level) {
    case 1: return "fast";
    case 2: return "decoy";
    case 3: return "strict";
    default: return "unspecified";
  }
}

bool parse_create_body(std::string_view body, storage::CollectionSpec* spec, std::string* error) {
  const auto fail = [&](const char* m) {
    if (error) *error = m;
    return false;
  };
  Json j;
  std::string why;
  if (!parse_json(body, &j, &why)) return fail("the body is not valid JSON");
  if (j.type != Json::Type::Object) return fail("the body must be a JSON object");
  static constexpr std::string_view kKnown[] = {"name",          "preset",         "dimension",
                                                "security_level", "key_tag",       "cluster_count",
                                                "filter_fields", "api_version"};
  for (const auto& f : j.fields) {
    bool known = false;
    for (const auto k : kKnown) known = known || f.first == k;
    if (!known) return fail("unknown field in the body");
  }
  storage::CollectionSpec s;
  const auto str_field = [&](const char* k, std::string* out) {
    const Json* v = j.find(k);
    if (!v || v->type != Json::Type::String) return false;
    *out = v->str;
    return true;
  };
  const auto num_field = [&](const char* k, std::uint32_t* out) {
    const Json* v = j.find(k);
    return v && get_uint32(*v, out);
  };
  if (!str_field("name", &s.name)) return fail("name must be a string");
  if (!str_field("preset", &s.preset)) return fail("preset must be a string");
  if (!str_field("key_tag", &s.key_tag)) return fail("key_tag must be a string");
  if (!num_field("dimension", &s.dimension)) return fail("dimension must be a whole number");
  if (!num_field("cluster_count", &s.cluster_count))
    return fail("cluster_count must be a whole number");
  const Json* sl = j.find("security_level");
  if (!sl) return fail("security_level is required: fast, decoy or strict");
  if (sl->type == Json::Type::String && sl->str == "fast")
    s.security_level = 1;
  else if (sl->type == Json::Type::String && sl->str == "decoy")
    s.security_level = 2;
  else if (sl->type == Json::Type::String && sl->str == "strict")
    s.security_level = 3;
  else
    return fail("security_level must be fast, decoy or strict");
  s.api_version = kApiVersion;
  if (const Json* v = j.find("api_version"); v && !get_uint32(*v, &s.api_version))
    return fail("api_version must be a whole number");
  if (const Json* ff = j.find("filter_fields")) {
    if (ff->type != Json::Type::Array) return fail("filter_fields must be an array");
    if (ff->items.size() > 1000) return fail("too many filter fields");
    for (const auto& item : ff->items) {
      if (item.type != Json::Type::Object || item.fields.size() != 2)
        return fail("a filter field is {\"field_id\": n, \"cardinality\": n}");
      const Json* id = item.find("field_id");
      const Json* card = item.find("cardinality");
      storage::FilterField f;
      if (!id || !card || !get_uint32(*id, &f.field_id) || !get_uint32(*card, &f.cardinality))
        return fail("a filter field is {\"field_id\": n, \"cardinality\": n}");
      s.filter_fields.push_back(f);
    }
  }
  *spec = std::move(s);
  return true;
}

}  // namespace ombra::server
