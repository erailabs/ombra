// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The REST admin API of collections, under /v1/collections (docs/site/reference/rest-api.md).
//
//   GET    /v1/collections         list      (read scope; a scoped key sees only its collection)
//   POST   /v1/collections         create    (write scope; JSON metadata only)
//   GET    /v1/collections/{name}  statistics (read scope)
//   DELETE /v1/collections/{name}  drop      (admin scope)
//
// It uses the gRPC service's own code for the work (OmbraService::*_core) and its policy for the
// checks (authorize_who, required_scope, Verified::allows_collection, validate_spec), so a key can
// do over REST exactly what it can do over gRPC. Nothing here reads or returns vectors, ids,
// documents or keys; logs carry request id, key id, route name, status and timing, never names.
#pragma once

#include <string>

#include "ombra/server/auth.hpp"
#include "ombra/server/http.hpp"
#include "ombra/server/limits.hpp"
#include "ombra/server/metrics.hpp"
#include "ombra/storage/store.hpp"
#include "rest_server.hpp"
#include "service.hpp"

namespace ombra::server {

class RestAdmin {
 public:
  // keys == nullptr: authentication is off (--no-auth), as for the service.
  RestAdmin(OmbraService& service, storage::Store& store, const ApiKeyStore* keys, Metrics& metrics)
      : service_(service), store_(store), keys_(keys), metrics_(metrics) {}
  // Handles one request whose path is under /v1/. `request_id` is already valid.
  RestReply handle(const HttpRequest& req, const std::string& request_id);

 private:
  OmbraService& service_;
  storage::Store& store_;
  const ApiKeyStore* keys_;
  Metrics& metrics_;
  LogLimiter refused_log_{std::chrono::seconds(5)};
};

}  // namespace ombra::server
