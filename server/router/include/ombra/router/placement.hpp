// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Placement of clusters on nodes and the `ombra-placement 1` map file.
//
// A cluster lives on the node with the highest random weight for (cluster id, node id)
// (rendezvous hashing). The weight depends on nothing else: not on the collection's data, not on
// the cluster centers, not on how many vectors a cluster holds. So the map says nothing about the
// data, and adding a node to n others moves about 1/(n+1) of the clusters, all of them to the
// new node. Pure C++: no gRPC, no OpenFHE; tested on the CPU.
//
// File format (UTF-8 text, one record per line, '#' starts a comment line):
//
//   ombra-placement 1
//   epoch 7
//   node n1 127.0.0.1:50051
//   node n2 127.0.0.1:50052
//   collection docs n1 n2          (optional; a collection that is not listed is on every node)
//
// The first line is the version header; any other version is refused. `epoch` counts map changes.
// Node ids are 1 to 64 characters from [A-Za-z0-9._-]; the order of the `node` lines is the
// order of the nodes (the first node also holds the alias table, see
// docs/site/operations/routing.md).
#pragma once
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace ombra::router {

struct PlacementError : std::runtime_error {
  using std::runtime_error::runtime_error;
};

struct Node {
  std::string id;
  std::string address;  // HOST:PORT of the node's gRPC port
};

struct CollectionEntry {
  std::string name;
  std::vector<std::string> nodes;  // node ids that hold the collection
};

// Highest random weight over `candidates` (node ids, at least one): index into `candidates`.
// `key` is a cluster id, or a hash of a blob id. Deterministic across platforms and runs.
std::size_t rendezvous_pick(std::uint64_t key, const std::vector<std::string>& candidates);

// 64-bit FNV-1a of bytes, for blob ids.
std::uint64_t hash_bytes(std::string_view bytes);

class PlacementMap {
 public:
  static constexpr const char* kMagic = "ombra-placement";
  static constexpr std::uint32_t kVersion = 1;

  std::uint64_t epoch = 0;
  std::vector<Node> nodes;
  std::vector<CollectionEntry> collections;

  // Throws PlacementError: wrong magic or version, malformed line, duplicate or invalid node id,
  // no node, a collection that names an unknown node, an address that is empty.
  static PlacementMap parse(std::string_view text);
  std::string serialize() const;
  static PlacementMap load(const std::string& path);
  // Temp file in the same directory, fsync, rename, fsync of the directory.
  void save(const std::string& path) const;

  // Indices into `nodes` of the nodes that hold `collection`, in map order.
  std::vector<std::size_t> nodes_of(std::string_view collection) const;
  // The node (index into `nodes`) that owns `cluster` of `collection`.
  std::size_t owner(std::string_view collection, std::uint32_t cluster) const;
  // The node that stores the blob with this id.
  std::size_t blob_owner(std::string_view collection, std::string_view blob_id) const;
  // Splits one round: result[i] holds, in the given order, the clusters of node `nodes[i]` (all
  // `nodes` entries are present, possibly with an empty subset). The subsets partition `clusters`.
  std::vector<std::vector<std::uint32_t>> split_round(
      std::string_view collection, const std::vector<std::uint32_t>& clusters) const;
  std::size_t index_of(std::string_view node_id) const;  // nodes.size() if unknown
};

bool valid_node_id(std::string_view id);

}  // namespace ombra::router
