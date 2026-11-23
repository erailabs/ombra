// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "router_service.hpp"

#include <google/protobuf/util/message_differencer.h>

#include <algorithm>
#include <chrono>
#include <future>
#include <set>

#include "ombra/pir/layout.hpp"
#include "ombra/pir/retrieval.hpp"
#include "ombra/server/log.hpp"
#include "ombra/server/retrieve.hpp"

namespace ombra::router {

namespace v1 = ombra::v1;
using server::Rpc;
using server::Scope;
namespace log = server::log;
using Stub = v1::Ombra::Stub;

struct RouterService::Call {
  std::string rid;
  server::Verified who;
  std::unique_ptr<log::CallScope> scope;
};

namespace {

grpc::Status unavailable() {
  return grpc::Status(grpc::StatusCode::UNAVAILABLE, "a node did not answer; retry");
}

// A failed call to a node: a node that does not answer is UNAVAILABLE (clients retry); an
// overloaded node passes RESOURCE_EXHAUSTED on; anything else is the router's own problem (logged
// by code only).
grpc::Status map_node(const grpc::Status& s) {
  if (s.ok()) return s;
  switch (s.error_code()) {
    case grpc::StatusCode::UNAVAILABLE:
    case grpc::StatusCode::DEADLINE_EXCEEDED:
    case grpc::StatusCode::CANCELLED:
    case grpc::StatusCode::ABORTED:
      return unavailable();
    case grpc::StatusCode::RESOURCE_EXHAUSTED:
      return grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED, "a node is overloaded");
    default:
      log::error("node call failed", {{"grpc_code", static_cast<int>(s.error_code())}});
      return grpc::Status(grpc::StatusCode::INTERNAL, "a node refused the router's call");
  }
}

void set_err(v1::Error* e, v1::ErrorCode c, const char* m) {
  e->set_code(c);
  e->set_message(m);
}

template <class R>
const v1::Error* inband(const R& r) {
  if constexpr (requires { r.has_error(); }) return r.has_error() ? &r.error() : nullptr;
  return nullptr;
}

template <class R>
struct Res {
  grpc::Status st;
  R resp;
};

// Runs f(node) for every node at once.
template <class R, class F>
std::vector<Res<R>> fan(const std::vector<std::size_t>& nodes, F&& f) {
  std::vector<std::future<Res<R>>> fs;
  for (auto n : nodes) fs.push_back(std::async(std::launch::async, [&f, n] { return f(n); }));
  std::vector<Res<R>> out;
  for (auto& x : fs) out.push_back(x.get());
  return out;
}

template <class R>
grpc::Status first_transport_failure(const std::vector<Res<R>>& rs) {
  for (const auto& r : rs)
    if (!r.st.ok()) return map_node(r.st);
  return grpc::Status::OK;
}

template <class R>
const v1::Error* first_inband(const std::vector<Res<R>>& rs) {
  for (const auto& r : rs)
    if (auto* e = inband(r.resp)) return e;
  return nullptr;
}

// Client-streaming calls to several nodes at once.
template <class Req, class Resp>
struct Writers {
  struct W {
    grpc::ClientContext cx;
    Resp resp;
    std::unique_ptr<grpc::ClientWriter<Req>> w;
    bool dead = false;
    grpc::Status st;
  };
  std::map<std::size_t, std::unique_ptr<W>> ws;
  W* get(std::size_t n) {
    auto it = ws.find(n);
    return it == ws.end() ? nullptr : it->second.get();
  }
  bool any_dead() const {
    for (const auto& [n, w] : ws)
      if (w->dead) return true;
    return false;
  }
  bool write(std::size_t n, const Req& r) {
    W* w = get(n);
    if (w->dead) return false;
    if (!w->w->Write(r)) w->dead = true;
    return !w->dead;
  }
  // abort: cancel every call (nothing more is coming). Otherwise half-close. Then collect.
  // Returns OK or the failure to report; *e gets the first in-band error of a node.
  grpc::Status finish(bool abort, const v1::Error** e) {
    *e = nullptr;
    for (auto& [n, w] : ws) {
      if (abort && !w->dead)
        w->cx.TryCancel();
      else if (!abort && !w->dead)
        w->w->WritesDone();
    }
    grpc::Status bad = grpc::Status::OK;
    for (auto& [n, w] : ws) {
      w->st = w->w->Finish();
      if (!w->st.ok() && bad.ok()) bad = map_node(w->st);
    }
    for (auto& [n, w] : ws)
      if (w->st.ok() && !*e) {
        *e = inband(w->resp);
        if (*e)
          log::warn("node refused a streamed call", {{"node", static_cast<std::uint64_t>(n)},
                                                     {"code", static_cast<int>((*e)->code())}});
      }
    if (*e) return grpc::Status::OK;  // a node's own refusal is the better answer
    return bad.ok() && abort ? unavailable() : bad;
  }
};

std::string prefixed(const std::string& node, const std::string& seg) { return node + "/" + seg; }

}  // namespace

RouterService::RouterService(PlacementMap m, const server::ApiKeyStore* keys, RouterConfig cfg)
    : keys_(keys),
      cfg_(std::move(cfg)),
      sessions_(cfg_.limits.max_sessions, cfg_.limits.max_sessions_per_key),
      map_(std::make_shared<const PlacementMap>(std::move(m))) {}

std::shared_ptr<const PlacementMap> RouterService::map() const {
  std::lock_guard lk(mu_);
  return map_;
}

void RouterService::set_map(PlacementMap m) {
  std::lock_guard lk(mu_);
  map_ = std::make_shared<const PlacementMap>(std::move(m));
}

std::shared_ptr<Stub> RouterService::stub(const PlacementMap& m, std::size_t node) {
  const std::string& addr = m.nodes[node].address;
  std::lock_guard lk(mu_);
  auto it = stubs_.find(addr);
  if (it != stubs_.end()) return it->second;
  grpc::ChannelArguments args;
  args.SetMaxReceiveMessageSize(static_cast<int>(cfg_.max_message_bytes));
  args.SetMaxSendMessageSize(static_cast<int>(cfg_.max_message_bytes));
  // A node that comes back is used again within about a second (gRPC's default back-off is longer).
  args.SetInt("grpc.initial_reconnect_backoff_ms", 200);
  args.SetInt("grpc.min_reconnect_backoff_ms", 200);
  args.SetInt("grpc.max_reconnect_backoff_ms", 1000);
  auto s = v1::Ombra::NewStub(grpc::CreateCustomChannel(addr, cfg_.node_creds, args));
  return stubs_[addr] = std::move(s);
}

void RouterService::arm(grpc::ClientContext& cx, const Call& call, bool deadline) const {
  if (!cfg_.node_token.empty()) cx.AddMetadata("authorization", "Bearer " + cfg_.node_token);
  cx.AddMetadata("x-request-id", call.rid);
  if (deadline)
    cx.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(cfg_.node_deadline_s));
}

grpc::Status RouterService::begin(grpc::ServerContext* ctx, Rpc rpc, Call* call) {
  const auto it = ctx->client_metadata().find("x-request-id");
  if (it != ctx->client_metadata().end() &&
      log::valid_request_id(std::string_view(it->second.data(), it->second.size())))
    call->rid.assign(it->second.data(), it->second.size());
  else
    call->rid = log::new_request_id();
  ctx->AddInitialMetadata("x-request-id", call->rid);
  call->scope = std::make_unique<log::CallScope>(call->rid, server::rpc_name(rpc));
  if (draining_.load())
    return grpc::Status(grpc::StatusCode::UNAVAILABLE, "the router is shutting down");
  std::string token;
  const auto a = ctx->client_metadata().find("authorization");
  if (a != ctx->client_metadata().end()) token.assign(a->second.data(), a->second.size());
  const auto r = server::authorize_who(keys_, token, server::required_scope(rpc), &call->who);
  if (r != server::AuthResult::Unauthenticated) call->scope->set_key_id(call->who.id);
  if (r == server::AuthResult::Unauthenticated) {
    std::uint64_t suppressed = 0;
    if (refused_log_.allow(&suppressed))
      log::warn("refused", {{"reason", "unauthenticated"},
                            {"not_logged", static_cast<std::uint64_t>(suppressed)}});
    return grpc::Status(grpc::StatusCode::UNAUTHENTICATED, "missing or invalid API key");
  }
  if (r == server::AuthResult::PermissionDenied)
    return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, "the API key's scope is too low");
  return grpc::Status::OK;
}

// A key limited to a collection matches its name exactly. Aliases are not resolved here, so a key
// limited to an alias does not work through the router.
grpc::Status RouterService::check_coll(const Call& call, std::string_view name) {
  if (call.who.allows_collection(name)) return grpc::Status::OK;
  log::warn("refused", {{"reason", "collection"}});
  return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                      "the API key is not allowed to use this collection");
}

#define BEGIN(RPC)                                                \
  Call call;                                                      \
  if (auto s_ = begin(ctx, Rpc::RPC, &call); !s_.ok()) return s_; \
  auto m = map();                                                 \
  (void)m

// ---- collections -------------------------------------------------------------------------------

grpc::Status RouterService::CreateCollection(grpc::ServerContext* ctx,
                                             const v1::CreateCollectionRequest* req,
                                             v1::CreateCollectionResponse* resp) {
  BEGIN(CreateCollection);
  if (auto d = check_coll(call, req->spec().name()); !d.ok()) return d;
  const auto idx = m->nodes_of(req->spec().name());
  auto rs = fan<v1::CreateCollectionResponse>(idx, [&](std::size_t n) {
    Res<v1::CreateCollectionResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    r.st = stub(*m, n)->CreateCollection(&cx, *req, &r.resp);
    return r;
  });
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  const v1::Error* other = nullptr;
  const Res<v1::CreateCollectionResponse>* created = nullptr;
  std::vector<std::size_t> existing;
  for (std::size_t i = 0; i < rs.size(); ++i) {
    const auto& r = rs[i].resp;
    if (!r.has_error())
      created = created ? created : &rs[i];
    else if (r.error().code() == v1::ERROR_COLLECTION_EXISTS)
      existing.push_back(idx[i]);
    else if (!other)
      other = &r.error();
  }
  if (other) {
    *resp->mutable_error() = *other;
    return grpc::Status::OK;
  }
  if (!created) {  // every node had it already: a real conflict
    set_err(resp->mutable_error(), v1::ERROR_COLLECTION_EXISTS, "collection exists");
    return grpc::Status::OK;
  }
  // Some nodes created it and some had it: an earlier create that was cut short. Safe to finish
  // only if what they hold is the same spec.
  for (auto n : existing) {
    grpc::ClientContext cx;
    arm(cx, call, true);
    v1::GetCollectionRequest g;
    g.set_name(req->spec().name());
    v1::GetCollectionResponse gr;
    if (auto st = stub(*m, n)->GetCollection(&cx, g, &gr); !st.ok()) return map_node(st);
    if (gr.has_error() ||
        !google::protobuf::util::MessageDifferencer::Equals(gr.info().spec(), req->spec())) {
      set_err(resp->mutable_error(), v1::ERROR_COLLECTION_EXISTS, "collection exists");
      return grpc::Status::OK;
    }
  }
  *resp->mutable_spec() = created->resp.spec();
  return grpc::Status::OK;
}

grpc::Status RouterService::GetCollection(grpc::ServerContext* ctx,
                                          const v1::GetCollectionRequest* req,
                                          v1::GetCollectionResponse* resp) {
  BEGIN(GetCollection);
  if (auto d = check_coll(call, req->name()); !d.ok()) return d;
  const auto one = [&](std::size_t n, const std::string& name) {
    Res<v1::GetCollectionResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    v1::GetCollectionRequest g;
    g.set_name(name);
    r.st = stub(*m, n)->GetCollection(&cx, g, &r.resp);
    return r;
  };
  // The alias table lives on the first node: it resolves an alias to the real name.
  std::string real = req->name();
  auto head = one(0, real);
  if (!head.st.ok()) return map_node(head.st);
  if (!head.resp.has_error() && head.resp.info().spec().name() != real)
    real = head.resp.info().spec().name();
  const auto idx = m->nodes_of(real);
  auto rs = fan<v1::GetCollectionResponse>(idx, [&](std::size_t n) { return one(n, real); });
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  if (auto* e = first_inband(rs)) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  auto* info = resp->mutable_info();
  *info = rs[0].resp.info();
  std::uint64_t v = 0, d = 0;
  bool keys = true;
  for (const auto& r : rs) {
    v += r.resp.info().vector_count();
    d += r.resp.info().deleted_count();
    keys = keys && r.resp.info().keys_uploaded();
  }
  info->set_vector_count(v);
  info->set_deleted_count(d);
  info->set_keys_uploaded(keys);
  return grpc::Status::OK;
}

grpc::Status RouterService::ListCollections(grpc::ServerContext* ctx,
                                            const v1::ListCollectionsRequest* req,
                                            v1::ListCollectionsResponse* resp) {
  BEGIN(ListCollections);
  std::vector<std::size_t> all(m->nodes.size());
  for (std::size_t i = 0; i < all.size(); ++i) all[i] = i;
  auto rs = fan<v1::ListCollectionsResponse>(all, [&](std::size_t n) {
    Res<v1::ListCollectionsResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    r.st = stub(*m, n)->ListCollections(&cx, *req, &r.resp);
    return r;
  });
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  std::map<std::string, v1::CollectionInfo> merged;
  for (const auto& r : rs)
    for (const auto& c : r.resp.collections()) {
      const auto& name = c.spec().name();
      if (!call.who.allows_collection(name)) continue;
      auto it = merged.find(name);
      if (it == merged.end()) {
        merged[name] = c;
        continue;
      }
      it->second.set_vector_count(it->second.vector_count() + c.vector_count());
      it->second.set_deleted_count(it->second.deleted_count() + c.deleted_count());
      it->second.set_keys_uploaded(it->second.keys_uploaded() && c.keys_uploaded());
    }
  for (auto& [n, c] : merged) *resp->add_collections() = std::move(c);
  return grpc::Status::OK;
}

grpc::Status RouterService::DropCollection(grpc::ServerContext* ctx,
                                           const v1::DropCollectionRequest* req,
                                           v1::DropCollectionResponse* resp) {
  BEGIN(DropCollection);
  if (auto d = check_coll(call, req->name()); !d.ok()) return d;
  const auto idx = m->nodes_of(req->name());
  const auto one = [&](std::size_t n) {
    Res<v1::DropCollectionResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    r.st = stub(*m, n)->DropCollection(&cx, *req, &r.resp);
    return r;
  };
  // The first node answers first: it may refuse because an alias points at the collection, and
  // then nothing may be dropped anywhere.
  auto head = one(idx[0]);
  if (!head.st.ok()) return map_node(head.st);
  bool dropped = !head.resp.has_error();
  if (head.resp.has_error() && head.resp.error().code() != v1::ERROR_COLLECTION_NOT_FOUND) {
    *resp->mutable_error() = head.resp.error();
    return grpc::Status::OK;
  }
  std::vector<std::size_t> rest(idx.begin() + 1, idx.end());
  auto rs = fan<v1::DropCollectionResponse>(rest, one);
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  for (const auto& r : rs) {
    if (!r.resp.has_error()) {
      dropped = true;
    } else if (r.resp.error().code() != v1::ERROR_COLLECTION_NOT_FOUND) {
      *resp->mutable_error() = r.resp.error();
      return grpc::Status::OK;
    }
  }
  if (!dropped)
    set_err(resp->mutable_error(), v1::ERROR_COLLECTION_NOT_FOUND, "collection not found");
  return grpc::Status::OK;
}

// ---- aliases: the table lives on the first node of the map
// ---------------------------------------

#define ALIAS_CALL(RPC, REQ, RESP)                                                                \
  grpc::Status RouterService::RPC(grpc::ServerContext* ctx, const v1::REQ* req, v1::RESP* resp) { \
    BEGIN(RPC);                                                                                   \
    grpc::ClientContext cx;                                                                       \
    arm(cx, call, true);                                                                          \
    return map_node(stub(*m, 0)->RPC(&cx, *req, resp));                                           \
  }
ALIAS_CALL(CreateAlias, CreateAliasRequest, CreateAliasResponse)
ALIAS_CALL(SwitchAlias, SwitchAliasRequest, SwitchAliasResponse)
ALIAS_CALL(DropAlias, DropAliasRequest, DropAliasResponse)
ALIAS_CALL(ListAliases, ListAliasesRequest, ListAliasesResponse)

// ---- keys, inserts, deletes
// ----------------------------------------------------------------------

grpc::Status RouterService::UploadKeys(grpc::ServerContext* ctx,
                                       grpc::ServerReader<v1::UploadKeysChunk>* in,
                                       v1::UploadKeysResponse* resp) {
  BEGIN(UploadKeys);
  v1::UploadKeysChunk c;
  if (!in->Read(&c)) {
    set_err(resp->mutable_error(), v1::ERROR_BAD_SEQUENCE, "no chunks");
    return grpc::Status::OK;
  }
  if (c.collection().empty()) {
    set_err(resp->mutable_error(), v1::ERROR_INVALID_ARGUMENT,
            "collection is required on the first chunk");
    return grpc::Status::OK;
  }
  if (auto d = check_coll(call, c.collection()); !d.ok()) return d;
  Writers<v1::UploadKeysChunk, v1::UploadKeysResponse> ws;
  for (auto n : m->nodes_of(c.collection())) {
    auto w = std::make_unique<decltype(ws)::W>();
    arm(w->cx, call, false);
    w->w = stub(*m, n)->UploadKeys(&w->cx, &w->resp);
    ws.ws[n] = std::move(w);
  }
  std::uint64_t bytes = 0;
  bool more = true;
  while (more && !ctx->IsCancelled()) {
    bytes += c.data().size();
    for (auto& [n, w] : ws.ws) ws.write(n, c);
    if (ws.any_dead()) break;
    more = in->Read(&c);
  }
  const v1::Error* e = nullptr;
  const bool abort = ws.any_dead() || ctx->IsCancelled();
  auto st = ws.finish(abort, &e);
  if (e)
    *resp->mutable_error() = *e;
  else if (!st.ok())
    return st;
  log::info("upload_keys routed",
            {{"nodes", static_cast<std::uint64_t>(ws.ws.size())}, {"bytes", bytes}});
  return grpc::Status::OK;
}

grpc::Status RouterService::Insert(grpc::ServerContext* ctx,
                                   grpc::ServerReader<v1::InsertRequest>* in,
                                   v1::InsertResponse* resp) {
  BEGIN(Insert);
  Writers<v1::InsertRequest, v1::InsertResponse> ws;
  std::vector<std::size_t> targets;
  std::uint64_t inserted = 0;
  v1::InsertRequest r;
  bool bad_seq = false;
  while (in->Read(&r)) {
    if (r.has_header()) {
      const auto& h = r.header();
      if (auto d = check_coll(call, h.collection()); !d.ok()) {
        const v1::Error* ignored = nullptr;
        ws.finish(true, &ignored);
        return d;
      }
      targets.clear();
      if (h.is_center())
        targets = m->nodes_of(h.collection());
      else
        targets = {m->owner(h.collection(), h.cluster_id())};
      inserted += static_cast<std::uint64_t>(h.ids_size());
      for (auto n : targets) {
        if (ws.get(n)) continue;
        auto w = std::make_unique<decltype(ws)::W>();
        arm(w->cx, call, false);
        w->w = stub(*m, n)->Insert(&w->cx, &w->resp);
        ws.ws[n] = std::move(w);
      }
    } else if (targets.empty()) {
      bad_seq = true;
      break;
    }
    for (auto n : targets) ws.write(n, r);
    if (ws.any_dead()) break;
  }
  if (bad_seq) {
    const v1::Error* e = nullptr;
    ws.finish(true, &e);
    set_err(resp->mutable_error(), v1::ERROR_BAD_SEQUENCE, "a column came before a header");
    return grpc::Status::OK;
  }
  const v1::Error* e = nullptr;
  auto st = ws.finish(ws.any_dead() || ctx->IsCancelled(), &e);
  if (e) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  if (!st.ok()) return st;
  resp->set_inserted(inserted);
  return grpc::Status::OK;
}

grpc::Status RouterService::Delete(grpc::ServerContext* ctx, const v1::DeleteRequest* req,
                                   v1::DeleteResponse* resp) {
  BEGIN(Delete);
  if (auto d = check_coll(call, req->collection()); !d.ok()) return d;
  auto rs = fan<v1::DeleteResponse>(m->nodes_of(req->collection()), [&](std::size_t n) {
    Res<v1::DeleteResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    r.st = stub(*m, n)->Delete(&cx, *req, &r.resp);
    return r;
  });
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  if (auto* e = first_inband(rs)) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  std::uint64_t n = 0;
  for (const auto& r : rs) n += r.resp.deleted();
  resp->set_deleted(n);
  return grpc::Status::OK;
}

// ---- search
// ----------------------------------------------------------------------------------------

namespace {
struct NodeSearch {
  grpc::ClientContext cx;
  std::unique_ptr<grpc::ClientReaderWriter<v1::SearchRequest, v1::SearchResponse>> rw;
  bool done = false;
  ~NodeSearch() {
    if (rw && !done) {
      cx.TryCancel();
      rw->Finish();
    }
  }
};
}  // namespace

grpc::Status RouterService::Search(
    grpc::ServerContext* ctx, grpc::ServerReaderWriter<v1::SearchResponse, v1::SearchRequest>* in) {
  BEGIN(Search);
  const auto send_error = [&](v1::ErrorCode c, const char* msg) {
    v1::SearchResponse out;
    set_err(out.mutable_error(), c, msg);
    in->Write(out);
    return grpc::Status::OK;
  };
  const auto guard = watchdog_.watch([ctx] { ctx->TryCancel(); }, cfg_.limits.search_idle_ms,
                                     cfg_.limits.search_total_ms);
  v1::SearchRequest req;
  if (!in->Read(&req)) return grpc::Status::OK;
  guard->touch();
  if (!req.has_start())
    return send_error(v1::ERROR_BAD_SEQUENCE, "the first message must be SearchStart");
  const std::string coll = req.start().collection();
  if (auto d = check_coll(call, coll); !d.ok()) return d;
  auto ticket = sessions_.try_acquire(call.who.id);
  if (!ticket.valid())
    return grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED, "too many concurrent searches");

  // The map of this stream: one version from start to end.
  const auto held = m->nodes_of(coll);
  std::vector<std::unique_ptr<NodeSearch>> ns(m->nodes.size());
  guard->busy(true);
  for (auto n : held) {
    ns[n] = std::make_unique<NodeSearch>();
    arm(ns[n]->cx, call, false);
    ns[n]->rw = stub(*m, n)->Search(&ns[n]->cx);
  }
  const auto fail_node = [&](std::size_t n) -> grpc::Status {
    // Finish() says why the node's stream ended: an overloaded node passes through, anything else
    // is "a node is missing". The round never continues without it.
    ns[n]->cx.TryCancel();
    const auto st = ns[n]->rw->Finish();
    ns[n]->done = true;
    log::warn("node did not answer", {{"node", m->nodes[n].id}});
    if (st.error_code() == grpc::StatusCode::RESOURCE_EXHAUSTED) return map_node(st);
    return send_error(v1::ERROR_UNAVAILABLE, "a node did not answer; retry");
  };
  // Every node expands the query at once; each answers with its center scores.
  for (auto n : held)
    if (!ns[n]->rw->Write(req)) return fail_node(n);
  std::vector<std::future<bool>> reads;
  std::vector<v1::SearchResponse> first(m->nodes.size());
  for (auto n : held)
    reads.push_back(std::async(std::launch::async, [&, n] { return ns[n]->rw->Read(&first[n]); }));
  std::vector<bool> got;
  for (auto& f : reads) got.push_back(f.get());
  for (std::size_t i = 0; i < held.size(); ++i)
    if (!got[i]) return fail_node(held[i]);
  for (auto n : held)
    if (first[n].has_error()) {
      in->Write(first[n]);
      return grpc::Status::OK;
    }
  for (auto n : held)
    if (!first[n].has_centers() ||
        first[n].centers().center_count() != first[held[0]].centers().center_count())
      return send_error(v1::ERROR_INTERNAL, "nodes disagree on the center blocks");
  // The center blocks are on every node: forward the first node's scores unchanged.
  if (!in->Write(first[held[0]])) return grpc::Status::OK;
  guard->busy(false);
  guard->touch();

  std::size_t rounds = 0;
  while (!ctx->IsCancelled() && in->Read(&req)) {
    guard->touch();
    if (ctx->IsCancelled() || guard->timed_out()) return grpc::Status::OK;
    if (++rounds > cfg_.limits.max_rounds)
      return send_error(v1::ERROR_SIZE_LIMIT, "too many rounds in one search stream");
    if (!req.has_chosen()) return send_error(v1::ERROR_BAD_SEQUENCE, "expected SearchChosen");
    const auto& ids = req.chosen().cluster_ids();
    if (ids.empty()) return send_error(v1::ERROR_INVALID_ARGUMENT, "no clusters chosen");
    if (static_cast<std::size_t>(ids.size()) >
        static_cast<std::size_t>(v1::LIMIT_MAX_CLUSTERS_PER_ROUND))
      return send_error(v1::ERROR_SIZE_LIMIT, "too many clusters in one round");
    guard->busy(true);
    std::vector<std::uint32_t> clusters;
    std::set<std::uint32_t> seen;
    for (auto id : ids)
      if (seen.insert(id).second) clusters.push_back(id);  // each cluster once, order kept
    const auto parts = m->split_round(coll, clusters);
    for (std::size_t i = 0; i < held.size(); ++i) {
      if (parts[i].empty()) continue;
      v1::SearchRequest sub;
      for (auto c : parts[i]) sub.mutable_chosen()->add_cluster_ids(c);
      if (!ns[held[i]]->rw->Write(sub)) return fail_node(held[i]);
    }
    // Answers go out in the order of the request, the same order on every replay.
    for (std::size_t k = 0; k < clusters.size(); ++k) {
      const std::size_t n = m->owner(coll, clusters[k]);
      v1::SearchResponse out;
      if (!ns[n]->rw->Read(&out)) return fail_node(n);
      if (out.has_error()) {
        in->Write(out);
        return grpc::Status::OK;
      }
      if (!out.has_scores() || out.scores().cluster_id() != clusters[k])
        return send_error(v1::ERROR_INTERNAL, "a node answered another cluster");
      out.mutable_scores()->set_last(k + 1 == clusters.size());
      if (!in->Write(out)) return grpc::Status::OK;
    }
    guard->busy(false);
    log::info("search_round routed", {{"clusters", static_cast<std::uint64_t>(clusters.size())},
                                      {"nodes", static_cast<std::uint64_t>(held.size())}});
  }
  for (auto n : held) {
    if (ns[n]->done) continue;
    ns[n]->rw->WritesDone();
    v1::SearchResponse drain;
    while (ns[n]->rw->Read(&drain)) {
    }
    ns[n]->rw->Finish();
    ns[n]->done = true;
  }
  return grpc::Status::OK;
}

// ---- private record retrieval
// ---------------------------------------------------------------------
//
// The scope is split by owner node; every node serves its own clusters as one part. The router
// learns the shape of each part from the public numbers of its Info (rows, row size, m, seed) and
// never which rows are wanted: query and answer bytes are forwarded unchanged, and only counts and
// sizes are logged.

namespace {
struct NodeRetrieve {
  grpc::ClientContext cx;
  std::unique_ptr<grpc::ClientReaderWriter<v1::RetrieveRecordsRequest, v1::RetrieveRecordsResponse>>
      rw;
  bool done = false;
  std::size_t n_cts = 0;
  std::uint32_t n_buckets = 0;
  void stop() {
    if (rw && !done) {
      cx.TryCancel();
      rw->Finish();
      done = true;
    }
  }
  ~NodeRetrieve() { stop(); }
};
}  // namespace

grpc::Status RouterService::RetrieveRecords(
    grpc::ServerContext* ctx,
    grpc::ServerReaderWriter<v1::RetrieveRecordsResponse, v1::RetrieveRecordsRequest>* in) {
  BEGIN(RetrieveRecords);
  const auto send_error = [&](v1::ErrorCode c, const char* msg) {
    v1::RetrieveRecordsResponse out;
    out.set_api_version(server::kApiVersion);
    set_err(out.mutable_error(), c, msg);
    in->Write(out);
    return grpc::Status::OK;
  };
  const auto send_status = [&](const server::Status& s) {
    return send_error(static_cast<v1::ErrorCode>(static_cast<int>(s.code)), s.message.c_str());
  };
  const auto guard = watchdog_.watch([ctx] { ctx->TryCancel(); }, cfg_.limits.search_idle_ms,
                                     cfg_.limits.search_total_ms);
  v1::RetrieveRecordsRequest req;
  if (!in->Read(&req)) return grpc::Status::OK;
  guard->touch();
  if (req.part_case() != v1::RetrieveRecordsRequest::kStart)
    return send_error(v1::ERROR_BAD_SEQUENCE, "the first message must be RetrieveRecordsStart");
  const std::string coll = req.start().collection();
  if (auto d = check_coll(call, coll); !d.ok()) return d;
  auto ticket = sessions_.try_acquire(call.who.id);
  if (!ticket.valid())
    return grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED, "too many concurrent retrievals");

  // The cluster count of the collection bounds the scope, as on a node. An empty name is left to
  // the validator.
  std::uint32_t cluster_count = 0;
  const auto held = coll.empty() ? std::vector<std::size_t>{} : m->nodes_of(coll);
  if (!held.empty()) {
    grpc::ClientContext cx;
    arm(cx, call, true);
    v1::GetCollectionRequest g;
    g.set_name(coll);
    v1::GetCollectionResponse gr;
    if (auto st = stub(*m, held[0])->GetCollection(&cx, g, &gr); !st.ok()) return map_node(st);
    if (gr.has_error()) {
      v1::RetrieveRecordsResponse out;
      out.set_api_version(server::kApiVersion);
      *out.mutable_error() = gr.error();
      in->Write(out);
      return grpc::Status::OK;
    }
    cluster_count = gr.info().spec().cluster_count();
  }
  server::RetrieveValidator check(cluster_count);
  if (auto st = check.accept(req); !st.ok()) return send_status(st);
  const v1::RetrieveRecordsStart& start = *check.start();
  if (held.empty()) return send_error(v1::ERROR_COLLECTION_NOT_FOUND, "collection not found");

  // Scope: clusters per owning node, in ascending node order.
  std::map<std::size_t, std::vector<std::uint32_t>> by_node;
  if (start.all_clusters()) {
    for (std::uint32_t c = 0; c < cluster_count; ++c) by_node[m->owner(coll, c)].push_back(c);
  } else {
    std::vector<std::uint32_t> ids(start.cluster_ids().begin(), start.cluster_ids().end());
    std::sort(ids.begin(), ids.end());
    for (auto c : ids) by_node[m->owner(coll, c)].push_back(c);
  }
  std::vector<std::unique_ptr<NodeRetrieve>> ns;
  std::vector<std::size_t> node_of;  // node index of each opened stream
  guard->busy(true);
  for (const auto& [n, clusters] : by_node) {
    auto r = std::make_unique<NodeRetrieve>();
    arm(r->cx, call, false);
    r->rw = stub(*m, n)->RetrieveRecords(&r->cx);
    node_of.push_back(n);
    ns.push_back(std::move(r));
  }
  const auto fail_node = [&](std::size_t i) -> grpc::Status {
    // Finish() says why the node's stream ended: an overloaded node passes through, anything else
    // is "a node is missing".
    ns[i]->cx.TryCancel();
    const auto st = ns[i]->rw->Finish();
    ns[i]->done = true;
    log::warn("node did not answer", {{"node", m->nodes[node_of[i]].id}});
    if (st.error_code() == grpc::StatusCode::RESOURCE_EXHAUSTED) return map_node(st);
    return send_error(v1::ERROR_UNAVAILABLE, "a node did not answer; retry");
  };
  {
    std::size_t i = 0;
    for (const auto& [n, clusters] : by_node) {
      v1::RetrieveRecordsRequest sub;
      sub.set_api_version(server::kApiVersion);
      auto* s = sub.mutable_start();
      s->set_collection(start.collection());
      s->set_key_tag(start.key_tag());
      s->set_pir_key_tag(start.pir_key_tag());
      for (auto c : clusters) s->add_cluster_ids(c);
      s->set_m(start.m());
      s->set_hash_seed(start.hash_seed());
      s->set_n_queries(0);
      if (!ns[i]->rw->Write(sub)) return fail_node(i);
      ++i;
    }
  }

  // One Info (or Error) from every node.
  std::vector<v1::RetrieveRecordsResponse> first(ns.size());
  {
    std::vector<std::future<bool>> reads;
    for (std::size_t i = 0; i < ns.size(); ++i)
      reads.push_back(
          std::async(std::launch::async, [&, i] { return ns[i]->rw->Read(&first[i]); }));
    std::vector<bool> got;
    for (auto& f : reads) got.push_back(f.get());
    for (std::size_t i = 0; i < ns.size(); ++i)
      if (!got[i]) return fail_node(i);
  }
  // A node whose part of the scope holds no record is left out of the call (the others still
  // answer); any other error of a node ends the call and is passed on as it is.
  std::vector<std::size_t> live;  // indices into ns, ascending node order
  for (std::size_t i = 0; i < ns.size(); ++i) {
    if (first[i].has_error()) {
      const auto& e = first[i].error();
      if (e.code() == v1::ERROR_INVALID_ARGUMENT && e.message() == server::kEmptyScopeMessage) {
        ns[i]->stop();
        continue;
      }
      in->Write(first[i]);
      return grpc::Status::OK;
    }
    if (!first[i].has_info()) return send_error(v1::ERROR_INTERNAL, "a node did not send its Info");
    live.push_back(i);
  }
  if (live.empty()) {  // no node has a record in scope: the nodes' own refusal
    in->Write(first[0]);
    return grpc::Status::OK;
  }

  // The shape of every part, from the public numbers of its Info.
  const std::vector<std::uint8_t> seed(start.hash_seed().begin(), start.hash_seed().end());
  std::size_t total_cts = 0;
  for (auto i : live) {
    const auto& info = first[i].info();
    if (info.n_rows() == 0 || info.row_size() == 0)
      return send_error(v1::ERROR_INTERNAL, "a node described an empty database");
    if (info.row_size() != first[live[0]].info().row_size())
      return send_error(v1::ERROR_BLOCK_MISMATCH, "the nodes hold records of different row sizes");
    try {
      const auto shape =
          pir::make_shape(info.n_rows(), start.m(), info.row_size(), seed, pir::retrieval_params());
      if (shape.buckets != info.n_buckets())
        return send_error(v1::ERROR_INTERNAL, "a node described another number of buckets");
      ns[i]->n_cts = shape.n_cts;
      ns[i]->n_buckets = info.n_buckets();
    } catch (const std::exception&) {
      return send_error(v1::ERROR_INTERNAL, "a node described an impossible shape");
    }
    if (ns[i]->n_cts > server::kMaxRetrieveQueries)
      return send_error(v1::ERROR_SIZE_LIMIT, "too many queries");
    total_cts += ns[i]->n_cts;
  }
  if (auto st = check.expect_queries(static_cast<std::uint32_t>(total_cts)); !st.ok())
    return send_status(st);

  // All Infos go out before any query is read.
  for (std::size_t p = 0; p < live.size(); ++p) {
    v1::RetrieveRecordsResponse out = first[live[p]];
    out.set_api_version(server::kApiVersion);
    out.mutable_info()->set_part(static_cast<std::uint32_t>(p));
    out.mutable_info()->set_n_parts(static_cast<std::uint32_t>(live.size()));
    if (!in->Write(out)) return grpc::Status::OK;
  }
  guard->busy(false);
  guard->touch();

  // The queries: the first n_cts of part 0 go to its node, and so on. Bytes are not looked at.
  std::uint64_t bytes = 0;
  std::size_t seen = 0, part = 0, in_part = 0;
  while (!ctx->IsCancelled() && in->Read(&req)) {
    guard->touch();
    if (ctx->IsCancelled() || guard->timed_out()) return grpc::Status::OK;
    if (auto st = check.accept(req); !st.ok()) return send_status(st);
    while (in_part == ns[live[part]]->n_cts) {  // this part is complete: the next one
      ++part;
      in_part = 0;
    }
    bytes += req.query().size();
    v1::RetrieveRecordsRequest sub;
    sub.set_api_version(server::kApiVersion);
    sub.mutable_query()->swap(*req.mutable_query());
    if (!ns[live[part]]->rw->Write(sub)) return fail_node(live[part]);
    ++in_part;
    ++seen;
  }
  if (ctx->IsCancelled() || guard->timed_out()) return grpc::Status::OK;
  if (auto st = check.finish(); !st.ok()) return send_status(st);
  guard->busy(true);
  for (auto i : live)
    if (!ns[i]->rw->WritesDone()) return fail_node(i);

  // The answers: part by part, in bucket order inside a part.
  std::uint64_t buckets = 0;
  for (std::size_t p = 0; p < live.size(); ++p) {
    const std::size_t i = live[p];
    for (std::uint32_t b = 0; b < ns[i]->n_buckets; ++b) {
      v1::RetrieveRecordsResponse out;
      if (!ns[i]->rw->Read(&out)) return fail_node(i);
      if (out.has_error()) {
        in->Write(out);
        return grpc::Status::OK;
      }
      if (!out.has_answer() || out.answer().bucket() != b)
        return send_error(v1::ERROR_INTERNAL, "a node answered another bucket");
      out.set_api_version(server::kApiVersion);
      out.mutable_answer()->set_part(static_cast<std::uint32_t>(p));
      bytes += out.answer().answer().size();
      ++buckets;
      if (ctx->IsCancelled()) return grpc::Status::OK;
      if (!in->Write(out)) return grpc::Status::OK;
    }
  }
  for (auto i : live) {
    v1::RetrieveRecordsResponse extra;
    if (ns[i]->rw->Read(&extra))
      return send_error(v1::ERROR_INTERNAL, "a node sent more than its Info announced");
    ns[i]->rw->Finish();
    ns[i]->done = true;
  }
  guard->busy(false);
  log::info("retrieve_records routed", {{"parts", static_cast<std::uint64_t>(live.size())},
                                        {"queries", static_cast<std::uint64_t>(seen)},
                                        {"buckets", buckets},
                                        {"bytes", bytes}});
  return grpc::Status::OK;
}

// ---- blocks
// ----------------------------------------------------------------------------------------

grpc::Status RouterService::ListBlocks(grpc::ServerContext* ctx, const v1::ListBlocksRequest* req,
                                       v1::ListBlocksResponse* resp) {
  BEGIN(ListBlocks);
  if (auto d = check_coll(call, req->collection()); !d.ok()) return d;
  const auto idx = m->nodes_of(req->collection());
  auto rs = fan<v1::ListBlocksResponse>(idx, [&](std::size_t n) {
    Res<v1::ListBlocksResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    r.st = stub(*m, n)->ListBlocks(&cx, *req, &r.resp);
    return r;
  });
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  if (auto* e = first_inband(rs)) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  resp->set_api_version(req->api_version());
  for (std::size_t i = 0; i < rs.size(); ++i)
    for (auto b : rs[i].resp.blocks()) {
      b.set_segment(prefixed(m->nodes[idx[i]].id, b.segment()));
      *resp->add_blocks() = std::move(b);
    }
  return grpc::Status::OK;
}

namespace {
// "node/segment" -> (node index, segment); false if the node is not in the map.
bool split_segment(const PlacementMap& m, const std::string& s, std::size_t* node,
                   std::string* seg) {
  const auto p = s.find('/');
  if (p == std::string::npos) return false;
  *node = m.index_of(std::string_view(s).substr(0, p));
  *seg = s.substr(p + 1);
  return *node < m.nodes.size();
}
}  // namespace

grpc::Status RouterService::GetBlock(grpc::ServerContext* ctx, const v1::GetBlockRequest* req,
                                     grpc::ServerWriter<v1::GetBlockResponse>* out) {
  BEGIN(GetBlock);
  if (auto d = check_coll(call, req->collection()); !d.ok()) return d;
  std::size_t n = 0;
  std::string seg;
  if (!split_segment(*m, req->segment(), &n, &seg)) {
    v1::GetBlockResponse r;
    set_err(r.mutable_error(), v1::ERROR_BLOCK_NOT_FOUND, "block not found");
    out->Write(r);
    return grpc::Status::OK;
  }
  v1::GetBlockRequest sub = *req;
  sub.set_segment(seg);
  grpc::ClientContext cx;
  arm(cx, call, false);
  auto rd = stub(*m, n)->GetBlock(&cx, sub);
  v1::GetBlockResponse r;
  while (rd->Read(&r))
    if (!out->Write(r)) {
      cx.TryCancel();
      break;
    }
  const auto st = rd->Finish();
  return ctx->IsCancelled() ? grpc::Status::OK : map_node(st);
}

grpc::Status RouterService::ReplaceBlock(grpc::ServerContext* ctx,
                                         grpc::ServerReader<v1::ReplaceBlockRequest>* in,
                                         v1::ReplaceBlockResponse* resp) {
  BEGIN(ReplaceBlock);
  v1::ReplaceBlockRequest r;
  if (!in->Read(&r) || !r.has_header()) {
    set_err(resp->mutable_error(), v1::ERROR_BAD_SEQUENCE, "the first message must be the header");
    return grpc::Status::OK;
  }
  if (auto d = check_coll(call, r.header().collection()); !d.ok()) return d;
  std::size_t node = m->nodes.size();
  v1::ReplaceBlockRequest head = r;
  head.mutable_header()->clear_segments();
  for (const auto& s : r.header().segments()) {
    std::size_t n = 0;
    std::string seg;
    if (!split_segment(*m, s, &n, &seg)) {
      set_err(resp->mutable_error(), v1::ERROR_BLOCK_NOT_FOUND, "block not found");
      return grpc::Status::OK;
    }
    if (node != m->nodes.size() && n != node) {
      set_err(resp->mutable_error(), v1::ERROR_BLOCK_MISMATCH, "blocks of different nodes");
      return grpc::Status::OK;
    }
    node = n;
    head.mutable_header()->add_segments(seg);
  }
  if (node == m->nodes.size()) {
    set_err(resp->mutable_error(), v1::ERROR_INVALID_ARGUMENT, "no segments");
    return grpc::Status::OK;
  }
  Writers<v1::ReplaceBlockRequest, v1::ReplaceBlockResponse> ws;
  auto w = std::make_unique<decltype(ws)::W>();
  arm(w->cx, call, false);
  w->w = stub(*m, node)->ReplaceBlock(&w->cx, &w->resp);
  auto* wp = w.get();
  ws.ws[node] = std::move(w);
  ws.write(node, head);
  while (!wp->dead && in->Read(&r)) ws.write(node, r);
  const v1::Error* e = nullptr;
  auto st = ws.finish(wp->dead || ctx->IsCancelled(), &e);
  if (e) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  if (!st.ok()) return st;
  resp->set_api_version(wp->resp.api_version());
  resp->set_segment(prefixed(m->nodes[node].id, wp->resp.segment()));
  return grpc::Status::OK;
}

// ---- blobs: placed by a hash of the id
// ---------------------------------------------------------------

grpc::Status RouterService::PutBlob(grpc::ServerContext* ctx,
                                    grpc::ServerReader<v1::PutBlobRequest>* in,
                                    v1::PutBlobResponse* resp) {
  BEGIN(PutBlob);
  v1::PutBlobRequest r;
  if (!in->Read(&r) || !r.has_header()) {
    set_err(resp->mutable_error(), v1::ERROR_BAD_SEQUENCE, "the first message must be the header");
    return grpc::Status::OK;
  }
  if (auto d = check_coll(call, r.header().collection()); !d.ok()) return d;
  const auto node = m->blob_owner(r.header().collection(), r.header().id());
  Writers<v1::PutBlobRequest, v1::PutBlobResponse> ws;
  auto w = std::make_unique<decltype(ws)::W>();
  arm(w->cx, call, false);
  w->w = stub(*m, node)->PutBlob(&w->cx, &w->resp);
  auto* wp = w.get();
  ws.ws[node] = std::move(w);
  ws.write(node, r);
  while (!wp->dead && in->Read(&r)) ws.write(node, r);
  const v1::Error* e = nullptr;
  auto st = ws.finish(wp->dead || ctx->IsCancelled(), &e);
  if (e) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  if (!st.ok()) return st;
  resp->set_api_version(wp->resp.api_version());
  return grpc::Status::OK;
}

grpc::Status RouterService::GetBlob(grpc::ServerContext* ctx, const v1::GetBlobRequest* req,
                                    grpc::ServerWriter<v1::GetBlobResponse>* out) {
  BEGIN(GetBlob);
  if (auto d = check_coll(call, req->collection()); !d.ok()) return d;
  grpc::ClientContext cx;
  arm(cx, call, false);
  auto rd = stub(*m, m->blob_owner(req->collection(), req->id()))->GetBlob(&cx, *req);
  v1::GetBlobResponse r;
  while (rd->Read(&r))
    if (!out->Write(r)) {
      cx.TryCancel();
      break;
    }
  const auto st = rd->Finish();
  return ctx->IsCancelled() ? grpc::Status::OK : map_node(st);
}

grpc::Status RouterService::DeleteBlobs(grpc::ServerContext* ctx, const v1::DeleteBlobsRequest* req,
                                        v1::DeleteBlobsResponse* resp) {
  BEGIN(DeleteBlobs);
  if (auto d = check_coll(call, req->collection()); !d.ok()) return d;
  std::map<std::size_t, v1::DeleteBlobsRequest> by_node;
  for (const auto& id : req->ids()) {
    auto& sub = by_node[m->blob_owner(req->collection(), id)];
    if (sub.collection().empty()) {
      sub.set_api_version(req->api_version());
      sub.set_collection(req->collection());
      sub.set_kind(req->kind());
    }
    sub.add_ids(id);
  }
  std::vector<std::size_t> idx;
  for (const auto& [n, s] : by_node) idx.push_back(n);
  auto rs = fan<v1::DeleteBlobsResponse>(idx, [&](std::size_t n) {
    Res<v1::DeleteBlobsResponse> r;
    grpc::ClientContext cx;
    arm(cx, call, true);
    r.st = stub(*m, n)->DeleteBlobs(&cx, by_node[n], &r.resp);
    return r;
  });
  if (auto s = first_transport_failure(rs); !s.ok()) return s;
  if (auto* e = first_inband(rs)) {
    *resp->mutable_error() = *e;
    return grpc::Status::OK;
  }
  std::uint64_t n = 0;
  for (const auto& r : rs) n += r.resp.deleted();
  resp->set_deleted(n);
  return grpc::Status::OK;
}

}  // namespace ombra::router
