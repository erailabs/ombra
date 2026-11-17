// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// CPU unit tests of the placement (T1 of decision 0029).
#include "ombra/router/placement.hpp"

#include <unistd.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <set>
#include <string>

#define CHECK(c)                                                                \
  do {                                                                          \
    if (!(c)) {                                                                 \
      std::fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #c); \
      std::exit(1);                                                             \
    }                                                                           \
  } while (0)

using namespace ombra::router;

static PlacementMap make_map(int n) {
  PlacementMap m;
  m.epoch = 1;
  for (int i = 0; i < n; ++i)
    m.nodes.push_back({"node" + std::to_string(i), "127.0.0.1:" + std::to_string(50000 + i)});
  return m;
}

static void test_deterministic_and_data_independent() {
  const auto a = make_map(3), b = make_map(3);
  for (std::uint32_t c = 0; c < 500; ++c) CHECK(a.owner("x", c) == b.owner("y", c));
  // The same answer for any collection name and in any call order: only (cluster, node ids) count.
  std::vector<std::uint32_t> fwd, rev;
  for (std::uint32_t c = 0; c < 200; ++c) fwd.push_back(a.owner("x", c));
  for (std::uint32_t c = 200; c-- > 0;) rev.push_back(a.owner("x", c));
  std::reverse(rev.begin(), rev.end());
  CHECK(fwd == rev);
  // A different node order in the file gives the same owner id for every cluster.
  auto r = make_map(3);
  std::reverse(r.nodes.begin(), r.nodes.end());
  for (std::uint32_t c = 0; c < 200; ++c)
    CHECK(a.nodes[a.owner("x", c)].id == r.nodes[r.owner("x", c)].id);
  // Every node gets some clusters.
  std::set<std::size_t> used;
  for (std::uint32_t c = 0; c < 200; ++c) used.insert(a.owner("x", c));
  CHECK(used.size() == 3);
}

static void test_round_partition() {
  const auto m = make_map(4);
  std::mt19937 rng(42);
  std::vector<std::uint32_t> round;
  for (int i = 0; i < 300; ++i) round.push_back(rng() % 1000);
  const auto parts = m.split_round("c", round);
  CHECK(parts.size() == 4);
  std::vector<std::uint32_t> all;
  for (std::size_t i = 0; i < parts.size(); ++i)
    for (auto c : parts[i]) {
      CHECK(m.owner("c", c) == i);
      all.push_back(c);
    }
  auto want = round;
  std::sort(all.begin(), all.end());
  std::sort(want.begin(), want.end());
  CHECK(all == want);  // exact partition: nothing dropped, nothing twice
  // The order inside a subset is the request order.
  for (const auto& p : parts) {
    std::size_t pos = 0;
    for (auto c : p) {
      while (pos < round.size() && round[pos] != c) ++pos;
      CHECK(pos < round.size());
      ++pos;
    }
  }
  // A collection on a subset of the nodes uses only those.
  auto sub = make_map(4);
  sub.collections.push_back({"c", {"node1", "node3"}});
  const auto sp = sub.split_round("c", round);
  CHECK(sp.size() == 2);
  CHECK(sp[0].size() + sp[1].size() == round.size());
  CHECK(sub.nodes_of("c") == (std::vector<std::size_t>{1, 3}));
  CHECK(sub.nodes_of("other").size() == 4);
}

static void test_adding_a_node_moves_one_nth() {
  for (int n : {2, 3, 5, 8}) {
    const auto before = make_map(n), after = make_map(n + 1);
    const std::uint32_t total = 20000;
    std::uint32_t moved = 0;
    for (std::uint32_t c = 0; c < total; ++c) {
      const auto o1 = before.nodes[before.owner("c", c)].id,
                 o2 = after.nodes[after.owner("c", c)].id;
      if (o1 != o2) {
        ++moved;
        CHECK(o2 == "node" + std::to_string(n));  // only ever to the new node
      }
    }
    const double frac = static_cast<double>(moved) / total, want = 1.0 / (n + 1);
    CHECK(frac > want * 0.9 && frac < want * 1.1);
  }
}

static void test_file() {
  auto m = make_map(3);
  m.epoch = 9;
  m.collections.push_back({"docs", {"node0", "node2"}});
  const auto text = m.serialize();
  CHECK(text.rfind("ombra-placement 1\n", 0) == 0);
  const auto back = PlacementMap::parse(text);
  CHECK(back.epoch == 9 && back.nodes.size() == 3 && back.collections.size() == 1);
  CHECK(back.serialize() == text);
  // Unknown versions and malformed files are refused.
  auto refused = [](const std::string& t) {
    try {
      PlacementMap::parse(t);
    } catch (const PlacementError&) {
      return true;
    }
    return false;
  };
  CHECK(refused("ombra-placement 2\nepoch 1\nnode a 127.0.0.1:1\n"));
  CHECK(refused("ombra-placement 0\nnode a 127.0.0.1:1\n"));
  CHECK(refused("ombra-placement\nnode a 127.0.0.1:1\n"));
  CHECK(refused("something 1\nnode a 127.0.0.1:1\n"));
  CHECK(refused(""));
  CHECK(refused("ombra-placement 1\nepoch 1\n"));                     // no node
  CHECK(refused("ombra-placement 1\nnode a x:1\nnode a y:2\n"));      // duplicate id
  CHECK(refused("ombra-placement 1\nnode a/b x:1\n"));                // bad id
  CHECK(refused("ombra-placement 1\nnode a x:1\ncollection c b\n"));  // unknown node
  CHECK(refused("ombra-placement 1\nnode a x:1\nbogus line\n"));
  CHECK(!refused("# comment\nombra-placement 1\n\nnode a x:1\n"));
  // Atomic save and load.
  char dir[] = "/tmp/ombra-placement-XXXXXX";
  CHECK(mkdtemp(dir) != nullptr);
  const std::string path = std::string(dir) + "/placement";
  m.save(path);
  const auto loaded = PlacementMap::load(path);
  CHECK(loaded.serialize() == text);
  CHECK(access((path + ".tmp").c_str(), F_OK) != 0);  // no temp file left behind
  bool threw = false;
  try {
    PlacementMap::load(std::string(dir) + "/missing");
  } catch (const PlacementError&) {
    threw = true;
  }
  CHECK(threw);
  std::remove(path.c_str());
  rmdir(dir);
}

static void test_blob_owner() {
  const auto m = make_map(3);
  std::set<std::size_t> used;
  for (int i = 0; i < 100; ++i) {
    const std::string id = "id-" + std::to_string(i);
    CHECK(m.blob_owner("c", id) == m.blob_owner("c", id));
    used.insert(m.blob_owner("c", id));
  }
  CHECK(used.size() == 3);
}

int main() {
  test_deterministic_and_data_independent();
  test_round_partition();
  test_adding_a_node_moves_one_nth();
  test_file();
  test_blob_owner();
  std::printf("placement_test: ok\n");
  return 0;
}
