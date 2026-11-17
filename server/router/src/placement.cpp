// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/router/placement.hpp"

#include <algorithm>
#include <cctype>
#include <sstream>

#include "ombra/storage/fsutil.hpp"

namespace ombra::router {

namespace {

std::uint64_t mix64(std::uint64_t x) {  // splitmix64 finalizer
  x += 0x9e3779b97f4a7c15ULL;
  x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
  x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
  return x ^ (x >> 31);
}

std::vector<std::string> split_ws(const std::string& line) {
  std::istringstream in(line);
  std::vector<std::string> out;
  std::string w;
  while (in >> w) out.push_back(w);
  return out;
}

}  // namespace

std::uint64_t hash_bytes(std::string_view bytes) {
  std::uint64_t h = 0xcbf29ce484222325ULL;
  for (unsigned char c : bytes) {
    h ^= c;
    h *= 0x100000001b3ULL;
  }
  return h;
}

std::size_t rendezvous_pick(std::uint64_t key, const std::vector<std::string>& candidates) {
  if (candidates.empty()) throw PlacementError("no node to place on");
  std::size_t best = 0;
  std::uint64_t best_w = 0;
  const std::uint64_t k = mix64(key);
  for (std::size_t i = 0; i < candidates.size(); ++i) {
    const std::uint64_t w = mix64(hash_bytes(candidates[i]) ^ k);
    // Ties (2^-64) go to the smaller id so the result never depends on the candidate order.
    if (i == 0 || w > best_w || (w == best_w && candidates[i] < candidates[best])) {
      best = i;
      best_w = w;
    }
  }
  return best;
}

bool valid_node_id(std::string_view id) {
  if (id.empty() || id.size() > 64) return false;
  return std::all_of(id.begin(), id.end(), [](unsigned char c) {
    return std::isalnum(c) || c == '.' || c == '_' || c == '-';
  });
}

PlacementMap PlacementMap::parse(std::string_view text) {
  PlacementMap m;
  std::istringstream in{std::string(text)};
  std::string line;
  bool header = false, have_epoch = false;
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    const auto w = split_ws(line);
    if (w.empty() || w[0][0] == '#') continue;
    if (!header) {
      if (w.size() != 2 || w[0] != kMagic) throw PlacementError("not an ombra-placement file");
      if (w[1] != std::to_string(kVersion))
        throw PlacementError("unsupported ombra-placement version " + w[1]);
      header = true;
    } else if (w[0] == "epoch") {
      std::uint64_t e = 0;
      if (w.size() != 2 || have_epoch || w[1].empty() || w[1].size() > 19 ||
          w[1].find_first_not_of("0123456789") != std::string::npos)
        throw PlacementError("bad epoch line");
      e = std::stoull(w[1]);
      m.epoch = e;
      have_epoch = true;
    } else if (w[0] == "node") {
      if (w.size() != 3) throw PlacementError("a node line is: node ID HOST:PORT");
      if (!valid_node_id(w[1])) throw PlacementError("invalid node id");
      if (m.index_of(w[1]) != m.nodes.size()) throw PlacementError("duplicate node id");
      m.nodes.push_back({w[1], w[2]});
    } else if (w[0] == "collection") {
      if (w.size() < 3) throw PlacementError("a collection line is: collection NAME NODE...");
      CollectionEntry c{w[1], {}};
      for (std::size_t i = 2; i < w.size(); ++i) c.nodes.push_back(w[i]);
      m.collections.push_back(std::move(c));
    } else {
      throw PlacementError("unknown line in the placement file");
    }
  }
  if (!header) throw PlacementError("empty placement file");
  if (m.nodes.empty()) throw PlacementError("the placement file lists no node");
  for (const auto& c : m.collections) {
    for (const auto& n : c.nodes)
      if (m.index_of(n) == m.nodes.size())
        throw PlacementError("a collection names an unknown node");
    std::vector<std::string> sorted = c.nodes;
    std::sort(sorted.begin(), sorted.end());
    if (std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end())
      throw PlacementError("a collection lists a node twice");
  }
  for (std::size_t i = 0; i < m.collections.size(); ++i)
    for (std::size_t j = i + 1; j < m.collections.size(); ++j)
      if (m.collections[i].name == m.collections[j].name)
        throw PlacementError("a collection is listed twice");
  return m;
}

std::string PlacementMap::serialize() const {
  std::ostringstream o;
  o << kMagic << ' ' << kVersion << '\n' << "epoch " << epoch << '\n';
  for (const auto& n : nodes) o << "node " << n.id << ' ' << n.address << '\n';
  for (const auto& c : collections) {
    o << "collection " << c.name;
    for (const auto& n : c.nodes) o << ' ' << n;
    o << '\n';
  }
  return o.str();
}

PlacementMap PlacementMap::load(const std::string& path) {
  std::vector<std::uint8_t> bytes;
  try {
    bytes = storage::read_file(path);
  } catch (const std::exception&) {
    throw PlacementError("cannot read the placement file");
  }
  return parse(std::string_view(reinterpret_cast<const char*>(bytes.data()), bytes.size()));
}

void PlacementMap::save(const std::string& path) const {
  const std::string s = serialize();
  parse(s);  // never write a map that cannot be read back
  storage::write_file_durable(path, std::vector<std::uint8_t>(s.begin(), s.end()));
}

std::size_t PlacementMap::index_of(std::string_view id) const {
  for (std::size_t i = 0; i < nodes.size(); ++i)
    if (nodes[i].id == id) return i;
  return nodes.size();
}

std::vector<std::size_t> PlacementMap::nodes_of(std::string_view collection) const {
  std::vector<std::size_t> out;
  for (const auto& c : collections) {
    if (c.name != collection) continue;
    for (std::size_t i = 0; i < nodes.size(); ++i)
      if (std::find(c.nodes.begin(), c.nodes.end(), nodes[i].id) != c.nodes.end()) out.push_back(i);
    return out;
  }
  for (std::size_t i = 0; i < nodes.size(); ++i) out.push_back(i);
  return out;
}

namespace {
std::size_t pick_in(const PlacementMap& m, const std::vector<std::size_t>& held,
                    std::uint64_t key) {
  std::vector<std::string> ids;
  ids.reserve(held.size());
  for (auto i : held) ids.push_back(m.nodes[i].id);
  return held[rendezvous_pick(key, ids)];
}
}  // namespace

std::size_t PlacementMap::owner(std::string_view collection, std::uint32_t cluster) const {
  return pick_in(*this, nodes_of(collection), cluster);
}

std::size_t PlacementMap::blob_owner(std::string_view collection, std::string_view blob_id) const {
  return pick_in(*this, nodes_of(collection), hash_bytes(blob_id));
}

std::vector<std::vector<std::uint32_t>> PlacementMap::split_round(
    std::string_view collection, const std::vector<std::uint32_t>& clusters) const {
  const auto held = nodes_of(collection);
  std::vector<std::vector<std::uint32_t>> out(held.size());
  for (auto c : clusters) {
    const std::size_t o = pick_in(*this, held, c);
    out[std::find(held.begin(), held.end(), o) - held.begin()].push_back(c);
  }
  return out;
}

}  // namespace ombra::router
