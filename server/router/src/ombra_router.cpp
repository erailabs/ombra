// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// ombra_router --placement FILE --listen HOST:PORT --data-dir D --node-key-file F
//              [--node-tls-ca F | --node-insecure] [--tls-cert F --tls-key F] [--insecure]
//              [--no-auth]
//              [--max-sessions N] [--max-sessions-per-key N] [--search-idle-s N] [--search-total-s
//              N]
//              [--max-rounds N] [--max-message-mb N] [--shutdown-grace-s N] [--log-format
//              text|json]
// ombra_router --create-api-key --scope admin|read|write --data-dir D [--collection NAME]
// ombra_router --version
//
// The router of decision 0029: it speaks ombra.v1 to clients and splits each call over the nodes
// named in the placement file (format "ombra-placement 1"). It holds no FHE key and never opens a
// ciphertext. Clients authenticate with API keys of the router's own key file (D/api_keys.v1 or
// v2, made with --create-api-key); the router calls the nodes with one node API key (admin scope)
// read from --node-key-file. SIGHUP re-reads the API key file and the placement file; streams that
// are open keep the map they started with. Prints "ombra_router listening on HOST:PORT" when ready.
#include <grpcpp/grpcpp.h>
#include <sys/stat.h>

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <sstream>
#include <thread>

#include "ombra/router/placement.hpp"
#include "ombra/server/auth.hpp"
#include "ombra/server/limits.hpp"
#include "ombra/server/log.hpp"
#include "router_service.hpp"

#ifndef OMBRA_VERSION
#define OMBRA_VERSION "unknown"
#endif

namespace {
std::atomic<bool> g_stop{false}, g_reload{false};
std::atomic<int> g_signals{0};
extern "C" void on_signal(int) {
  g_signals.fetch_add(1);
  g_stop.store(true);
}
extern "C" void on_hup(int) { g_reload.store(true); }

std::string read_file(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("cannot read " + path);
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

// The node key: one line, a file nobody but the owner can read.
std::string read_secret_file(const std::string& path) {
  struct stat st {};
  if (::stat(path.c_str(), &st) != 0) throw std::runtime_error("cannot read " + path);
  if (st.st_mode & 077) throw std::runtime_error(path + " is readable by group or others");
  std::string s = read_file(path);
  while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) s.pop_back();
  if (s.empty()) throw std::runtime_error(path + " is empty");
  return s;
}
}  // namespace

int main(int argc, char** argv) {
  using namespace ombra;
  std::string data_dir, listen, placement, node_key_file, node_ca, tls_cert, tls_key, scope_arg,
      key_collection, log_format_arg;
  std::uint64_t max_message_mb = 16, shutdown_grace_s = 30, search_idle_s = 60,
                search_total_s = 600;
  server::ServiceLimits limits;
  bool insecure = false, no_auth = false, node_insecure = false, create_key = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto next = [&]() -> std::string {
      if (i + 1 >= argc) {
        std::fprintf(stderr, "missing value for %s\n", a.c_str());
        std::exit(2);
      }
      return argv[++i];
    };
    const auto num = [&](std::uint64_t lo, std::uint64_t hi) -> std::uint64_t {
      const std::string v = next();
      std::uint64_t out = 0;
      if (!server::parse_uint(v, lo, hi, &out)) {
        std::fprintf(stderr, "invalid value for %s: expected a number from %llu to %llu\n",
                     a.c_str(), static_cast<unsigned long long>(lo),
                     static_cast<unsigned long long>(hi));
        std::exit(2);
      }
      return out;
    };
    if (a == "--version") {
      std::printf("ombra_router %s\n", OMBRA_VERSION);
      return 0;
    } else if (a == "--data-dir")
      data_dir = next();
    else if (a == "--listen")
      listen = next();
    else if (a == "--placement")
      placement = next();
    else if (a == "--node-key-file")
      node_key_file = next();
    else if (a == "--node-tls-ca")
      node_ca = next();
    else if (a == "--node-insecure")
      node_insecure = true;
    else if (a == "--tls-cert")
      tls_cert = next();
    else if (a == "--tls-key")
      tls_key = next();
    else if (a == "--max-message-mb")
      max_message_mb = num(8, 2000);
    else if (a == "--shutdown-grace-s")
      shutdown_grace_s = num(0, 86400);
    else if (a == "--max-sessions")
      limits.max_sessions = num(1, 100000);
    else if (a == "--max-sessions-per-key")
      limits.max_sessions_per_key = num(1, 100000);
    else if (a == "--search-idle-s")
      search_idle_s = num(0, 86400);
    else if (a == "--search-total-s")
      search_total_s = num(0, 7 * 86400);
    else if (a == "--max-rounds")
      limits.max_rounds = num(1, 1000000);
    else if (a == "--scope")
      scope_arg = next();
    else if (a == "--collection")
      key_collection = next();
    else if (a == "--log-format")
      log_format_arg = next();
    else if (a == "--create-api-key")
      create_key = true;
    else if (a == "--insecure")
      insecure = true;
    else if (a == "--no-auth")
      no_auth = true;
    else {
      std::fprintf(stderr, "unknown argument %s\n", a.c_str());
      return 2;
    }
  }
  if (!log_format_arg.empty()) {
    server::log::Format lf;
    if (!server::log::parse_format(log_format_arg, &lf)) {
      std::fprintf(stderr, "invalid value for --log-format: expected text or json\n");
      return 2;
    }
    server::log::set_format(lf);
  }
  if (create_key) {
    server::Scope scope;
    if (data_dir.empty() || !server::parse_scope(scope_arg, &scope)) {
      std::fprintf(stderr,
                   "usage: ombra_router --create-api-key --scope admin|read|write --data-dir D "
                   "[--collection NAME]\n");
      return 2;
    }
    try {
      std::string id;
      std::printf("%s\n", server::create_api_key(data_dir, scope, &id, key_collection).c_str());
      std::fflush(stdout);
      std::fprintf(stderr, "created %s API key %s (shown once; only its salted hash is stored)\n",
                   scope_arg.c_str(), id.c_str());
    } catch (const std::exception& e) {
      std::fprintf(stderr, "fatal: %s\n", e.what());
      return 1;
    }
    return 0;
  }
  if (data_dir.empty() || listen.empty() || placement.empty()) {
    std::fprintf(stderr,
                 "usage: ombra_router --placement FILE --listen HOST:PORT --data-dir D "
                 "--node-key-file F --tls-cert F --tls-key F\n");
    return 2;
  }
  if (tls_cert.empty() != tls_key.empty()) {
    std::fprintf(stderr, "--tls-cert and --tls-key go together\n");
    return 2;
  }
  const bool tls = !tls_cert.empty();
  if (!tls && !insecure) {
    std::fprintf(stderr,
                 "refusing to start without TLS: pass --tls-cert and --tls-key (or --insecure)\n");
    return 2;
  }
  if ((!tls || no_auth) && !server::is_loopback_listen(listen)) {
    std::fprintf(stderr,
                 "refusing to listen on a non-loopback address with --insecure or --no-auth\n");
    return 2;
  }
  if (node_insecure == !node_ca.empty()) {
    std::fprintf(stderr, "pass exactly one of --node-tls-ca FILE and --node-insecure\n");
    return 2;
  }
  std::unique_ptr<server::ApiKeyStore> keys;
  if (!no_auth) {
    try {
      keys = std::make_unique<server::ApiKeyStore>(
          server::ApiKeyStore::load(server::ApiKeyStore::path_in(data_dir)));
    } catch (const std::exception& e) {
      std::fprintf(
          stderr,
          "no usable API key file (%s): create one with --create-api-key, or pass --no-auth\n",
          e.what());
      return 2;
    }
    if (keys->size() == 0) {
      std::fprintf(stderr, "the API key file is empty: create a key with --create-api-key\n");
      return 2;
    }
  }
  if (!tls) server::log::warn("--insecure: client connections are NOT encrypted");
  if (no_auth) server::log::warn("--no-auth: anyone who can connect has full access");
  if (node_insecure)
    server::log::warn("--node-insecure: connections to the nodes are NOT encrypted");
  ::umask(077);
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);
  std::signal(SIGHUP, on_hup);
  try {
    router::RouterConfig cfg;
    if (!node_key_file.empty()) cfg.node_token = read_secret_file(node_key_file);
    if (node_insecure) {
      cfg.node_creds = grpc::InsecureChannelCredentials();
    } else {
      grpc::SslCredentialsOptions o;
      o.pem_root_certs = read_file(node_ca);
      cfg.node_creds = grpc::SslCredentials(o);
    }
    limits.search_idle_ms = search_idle_s * 1000;
    limits.search_total_ms = search_total_s * 1000;
    cfg.limits = limits;
    cfg.max_message_bytes = static_cast<std::size_t>(max_message_mb) << 20;
    router::RouterService service(router::PlacementMap::load(placement), keys.get(), cfg);

    std::shared_ptr<grpc::ServerCredentials> creds;
    if (tls) {
      grpc::SslServerCredentialsOptions opts;
      opts.pem_key_cert_pairs.push_back({read_file(tls_key), read_file(tls_cert)});
      creds = grpc::SslServerCredentials(opts);
    } else {
      creds = grpc::InsecureServerCredentials();
    }
    grpc::ServerBuilder builder;
    int port = 0;
    builder.AddListeningPort(listen, creds, &port);
    builder.SetMaxReceiveMessageSize(static_cast<int>(max_message_mb << 20));
    builder.SetMaxSendMessageSize(static_cast<int>(max_message_mb << 20));
    builder.SetResourceQuota(grpc::ResourceQuota("ombra-router").SetMaxThreads(256));
    builder.RegisterService(&service);
    std::unique_ptr<grpc::Server> srv = builder.BuildAndStart();
    if (!srv || port == 0) {
      std::fprintf(stderr, "cannot listen on %s\n", listen.c_str());
      return 1;
    }
    std::printf("ombra_router listening on %s:%d\n", listen.substr(0, listen.rfind(':')).c_str(),
                port);
    std::fflush(stdout);
    while (!g_stop.load()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      if (!g_reload.exchange(false)) continue;
      try {
        if (keys) keys->reload(server::ApiKeyStore::path_in(data_dir));
        auto m = router::PlacementMap::load(placement);
        const auto epoch = m.epoch;
        service.set_map(std::move(m));
        server::log::info("reloaded keys and placement",
                          {{"epoch", static_cast<std::uint64_t>(epoch)}});
      } catch (const std::exception& e) {
        server::log::error("reload failed, keeping the loaded keys and map", {{"error", e.what()}});
      }
    }
    server::log::info("shutdown requested: draining",
                      {{"grace_s", static_cast<std::uint64_t>(shutdown_grace_s)}});
    service.begin_drain();
    std::atomic<bool> drained{false};
    std::thread drain([&] {
      srv->Shutdown(std::chrono::system_clock::now() + std::chrono::seconds(shutdown_grace_s));
      drained.store(true);
    });
    while (!drained.load() && g_signals.load() < 2)
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
    if (!drained.load()) std::_Exit(1);
    drain.join();
    srv->Wait();
  } catch (const std::exception& e) {
    std::fprintf(stderr, "fatal: %s\n", e.what());
    return 1;
  }
  return 0;
}
