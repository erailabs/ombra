// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "rest_admin.hpp"

#include <chrono>
#include <string_view>

#include "ombra/server/log.hpp"
#include "ombra/server/policy.hpp"
#include "ombra/server/rest_api.hpp"

namespace ombra::server {

namespace {

using Clock = std::chrono::steady_clock;

RestReply error_reply(int status, std::string_view code, std::string_view message,
                      const std::string& rid, std::string headers = {}) {
  return {status, rest_error_body(code, message, rid), std::move(headers)};
}

RestReply proto_error_reply(const v1::Error& e, const std::string& rid) {
  const Err code = static_cast<Err>(static_cast<int>(e.code()));
  return error_reply(err_http_status(code), err_name(code), e.message(), rid);
}

void put_spec(std::string* o, const v1::CollectionSpec& s) {
  *o += "\"name\":" + json_string(s.name());
  *o += ",\"preset\":" + json_string(s.preset());
  *o += ",\"dimension\":" + std::to_string(s.dimension());
  *o += ",\"security_level\":" +
        json_string(security_level_name(static_cast<std::uint32_t>(s.security_level())));
  *o += ",\"key_tag\":" + json_string(s.key_tag());
  *o += ",\"cluster_count\":" + std::to_string(s.cluster_count());
  *o += ",\"filter_fields\":[";
  for (int i = 0; i < s.filter_fields_size(); ++i) {
    if (i) *o += ",";
    *o += "{\"field_id\":" + std::to_string(s.filter_fields(i).field_id()) +
          ",\"cardinality\":" + std::to_string(s.filter_fields(i).cardinality()) + "}";
  }
  *o += "]";
}

// Counts only. vector_count includes deleted vectors until the next compaction, like over gRPC.
std::string info_json(const v1::CollectionInfo& info, const std::string& extra = {}) {
  std::string o = "{";
  put_spec(&o, info.spec());
  const std::uint64_t total = info.vector_count(), dead = info.deleted_count();
  o += ",\"vector_count\":" + std::to_string(total);
  o += ",\"deleted_count\":" + std::to_string(dead);
  o += ",\"live_count\":" + std::to_string(total >= dead ? total - dead : 0);
  o += std::string(",\"keys_uploaded\":") + (info.keys_uploaded() ? "true" : "false");
  return o + extra + "}";
}

}  // namespace

RestReply RestAdmin::handle(const HttpRequest& req, const std::string& rid) {
  const auto t0 = Clock::now();
  const Route route = match_route(req.path);
  if (route.kind != RouteKind::List && route.kind != RouteKind::Collection)
    return error_reply(404, "NOT_FOUND", "no such route", rid);

  // Route and method to the RPC whose scope rule applies.
  const bool list = route.kind == RouteKind::List;
  Rpc rpc;
  const char* what;
  if (req.method == "GET") {
    rpc = list ? Rpc::ListCollections : Rpc::GetCollection;
    what = list ? "list_collections" : "get_collection";
  } else if (req.method == "POST" && list) {
    rpc = Rpc::CreateCollection;
    what = "create_collection";
  } else if (req.method == "DELETE" && !list) {
    rpc = Rpc::DropCollection;
    what = "drop_collection";
  } else {
    return error_reply(405, "METHOD_NOT_ALLOWED", "method not allowed for this route", rid,
                       std::string("Allow: ") + (list ? "GET, POST" : "GET, DELETE") + "\r\n");
  }

  log::CallScope scope(rid, rpc_name(rpc));
  Verified who;
  const AuthResult a = authorize_who(keys_, req.authorization, required_scope(rpc), &who);
  if (a != AuthResult::Unauthenticated) scope.set_key_id(who.id);
  if (a == AuthResult::Unauthenticated) {
    metrics_.count_unauthenticated();
    std::uint64_t suppressed = 0;
    if (refused_log_.allow(&suppressed))
      log::warn("refused", {{"reason", "unauthenticated"},
                            {"not_logged", static_cast<std::uint64_t>(suppressed)}});
    return error_reply(401, "UNAUTHENTICATED", "missing or invalid API key", rid);
  }
  const auto denied = [&](const char* reason, const char* message) {
    metrics_.count_permission_denied();
    log::warn("refused", {{"reason", reason}});
    return error_reply(403, "PERMISSION_DENIED", message, rid);
  };
  if (a == AuthResult::PermissionDenied) return denied("scope", "the API key's scope is too low");
  // The same answer whether the collection exists or not (no probing of other names).
  constexpr const char* kCollectionDenied = "the API key is not allowed to use this collection";
  if (!list && !who.allows_collection(route.name)) return denied("collection", kCollectionDenied);

  RestReply out;
  bool failed = false;
  if (rpc == Rpc::ListCollections) {
    v1::ListCollectionsResponse resp;
    if (!service_.list_collections_core(who, &resp)) {
      failed = true;
      out = error_reply(500, "INTERNAL", "internal error", rid);
    } else {
      std::string o = "{\"collections\":[";
      for (int i = 0; i < resp.collections_size(); ++i) {
        if (i) o += ",";
        o += info_json(resp.collections(i));
      }
      out = {200, o + "]}", {}};
    }
  } else if (rpc == Rpc::GetCollection) {
    v1::GetCollectionResponse resp;
    service_.get_collection_core(route.name, &resp);
    if (resp.has_error()) {
      failed = true;
      out = proto_error_reply(resp.error(), rid);
    } else {
      std::string extra;
      try {
        std::uint64_t n = 0, bytes = 0;
        store_.blob_totals(route.name, &n, &bytes);
        extra = ",\"disk_bytes\":" + std::to_string(store_.disk_bytes(route.name)) +
                ",\"blob_count\":" + std::to_string(n) + ",\"blob_bytes\":" + std::to_string(bytes);
      } catch (const std::exception&) {  // e.g. dropped meanwhile: leave the sizes out
      }
      out = {200, info_json(resp.info(), extra), {}};
    }
  } else if (rpc == Rpc::CreateCollection) {
    if (req.body.empty()) {
      failed = true;
      out = error_reply(400, "INVALID_ARGUMENT", "a JSON body is required", rid);
    } else {
      storage::CollectionSpec spec;
      std::string why;
      if (!parse_create_body(req.body, &spec, &why)) {
        failed = true;
        out = error_reply(400, "INVALID_ARGUMENT", why, rid);
      } else if (!who.allows_collection(spec.name)) {
        return denied("collection", kCollectionDenied);
      } else {
        v1::CreateCollectionRequest preq;
        auto* ps = preq.mutable_spec();
        ps->set_api_version(spec.api_version);
        ps->set_name(spec.name);
        ps->set_preset(spec.preset);
        ps->set_dimension(spec.dimension);
        ps->set_security_level(static_cast<v1::SecurityLevel>(spec.security_level));
        for (const auto& f : spec.filter_fields) {
          auto* q = ps->add_filter_fields();
          q->set_field_id(f.field_id);
          q->set_cardinality(f.cardinality);
        }
        ps->set_key_tag(spec.key_tag);
        ps->set_cluster_count(spec.cluster_count);
        v1::CreateCollectionResponse resp;
        service_.create_collection_core(*ps, &resp);
        if (resp.has_error()) {
          failed = true;
          out = proto_error_reply(resp.error(), rid);
        } else {
          v1::CollectionInfo info;
          *info.mutable_spec() = resp.spec();
          out = {201, info_json(info), {}};
        }
      }
    }
  } else {  // DropCollection
    v1::DropCollectionResponse resp;
    service_.drop_collection_core(route.name, &resp);
    if (resp.has_error()) {
      failed = true;
      out = proto_error_reply(resp.error(), rid);
    } else {
      out = {200, "{\"dropped\":true}", {}};
    }
  }
  const double ms = std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
  metrics_.record(rpc, failed, ms);
  log::info("rest request", {{"route", what}, {"status", out.status}, {"ms", ms}});
  return out;
}

}  // namespace ombra::server
