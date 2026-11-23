// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The router: speaks ombra.v1 to clients and splits every call over the nodes by
// the placement map. It holds no key of any FHE scheme and never opens a ciphertext: score blocks
// pass through as bytes. Logs carry counts, sizes and timings only.
#pragma once

#include <grpcpp/grpcpp.h>

#include <atomic>
#include <map>
#include <memory>
#include <mutex>
#include <string>

#include "ombra/router/placement.hpp"
#include "ombra/server/auth.hpp"
#include "ombra/server/limits.hpp"
#include "ombra/v1/ombra.grpc.pb.h"

namespace ombra::router {

struct RouterConfig {
  std::string node_token;  // API key the router presents to every node (admin scope)
  std::shared_ptr<grpc::ChannelCredentials> node_creds;
  server::ServiceLimits limits;
  std::size_t max_message_bytes = std::size_t{16} << 20;
  std::uint64_t node_deadline_s = 120;  // deadline of unary node calls
};

class RouterService final : public v1::Ombra::Service {
 public:
  RouterService(PlacementMap map, const server::ApiKeyStore* keys, RouterConfig cfg);
  void set_map(PlacementMap map);  // SIGHUP: streams already open keep the map they started with
  void begin_drain() { draining_.store(true); }

  grpc::Status CreateCollection(grpc::ServerContext*, const v1::CreateCollectionRequest*,
                                v1::CreateCollectionResponse*) override;
  grpc::Status GetCollection(grpc::ServerContext*, const v1::GetCollectionRequest*,
                             v1::GetCollectionResponse*) override;
  grpc::Status ListCollections(grpc::ServerContext*, const v1::ListCollectionsRequest*,
                               v1::ListCollectionsResponse*) override;
  grpc::Status DropCollection(grpc::ServerContext*, const v1::DropCollectionRequest*,
                              v1::DropCollectionResponse*) override;
  grpc::Status CreateAlias(grpc::ServerContext*, const v1::CreateAliasRequest*,
                           v1::CreateAliasResponse*) override;
  grpc::Status SwitchAlias(grpc::ServerContext*, const v1::SwitchAliasRequest*,
                           v1::SwitchAliasResponse*) override;
  grpc::Status DropAlias(grpc::ServerContext*, const v1::DropAliasRequest*,
                         v1::DropAliasResponse*) override;
  grpc::Status ListAliases(grpc::ServerContext*, const v1::ListAliasesRequest*,
                           v1::ListAliasesResponse*) override;
  grpc::Status UploadKeys(grpc::ServerContext*, grpc::ServerReader<v1::UploadKeysChunk>*,
                          v1::UploadKeysResponse*) override;
  grpc::Status Insert(grpc::ServerContext*, grpc::ServerReader<v1::InsertRequest>*,
                      v1::InsertResponse*) override;
  grpc::Status Delete(grpc::ServerContext*, const v1::DeleteRequest*, v1::DeleteResponse*) override;
  grpc::Status Search(grpc::ServerContext*,
                      grpc::ServerReaderWriter<v1::SearchResponse, v1::SearchRequest>*) override;
  grpc::Status ListBlocks(grpc::ServerContext*, const v1::ListBlocksRequest*,
                          v1::ListBlocksResponse*) override;
  grpc::Status GetBlock(grpc::ServerContext*, const v1::GetBlockRequest*,
                        grpc::ServerWriter<v1::GetBlockResponse>*) override;
  grpc::Status RetrieveRecords(
      grpc::ServerContext*,
      grpc::ServerReaderWriter<v1::RetrieveRecordsResponse, v1::RetrieveRecordsRequest>*) override;
  grpc::Status ReplaceBlock(grpc::ServerContext*, grpc::ServerReader<v1::ReplaceBlockRequest>*,
                            v1::ReplaceBlockResponse*) override;
  grpc::Status PutBlob(grpc::ServerContext*, grpc::ServerReader<v1::PutBlobRequest>*,
                       v1::PutBlobResponse*) override;
  grpc::Status GetBlob(grpc::ServerContext*, const v1::GetBlobRequest*,
                       grpc::ServerWriter<v1::GetBlobResponse>*) override;
  grpc::Status DeleteBlobs(grpc::ServerContext*, const v1::DeleteBlobsRequest*,
                           v1::DeleteBlobsResponse*) override;

 private:
  struct Call;
  std::shared_ptr<const PlacementMap> map() const;
  std::shared_ptr<v1::Ombra::Stub> stub(const PlacementMap& m, std::size_t node);
  void arm(grpc::ClientContext& cx, const Call& call, bool deadline) const;
  grpc::Status begin(grpc::ServerContext* ctx, server::Rpc rpc, Call* call);
  static grpc::Status check_coll(const Call& call, std::string_view name);

  const server::ApiKeyStore* keys_;
  RouterConfig cfg_;
  server::SessionLimiter sessions_;
  server::Watchdog watchdog_;
  server::LogLimiter refused_log_{std::chrono::seconds(5)};
  std::atomic<bool> draining_{false};
  mutable std::mutex mu_;
  std::shared_ptr<const PlacementMap> map_;
  std::map<std::string, std::shared_ptr<v1::Ombra::Stub>> stubs_;  // by address
};

}  // namespace ombra::router
