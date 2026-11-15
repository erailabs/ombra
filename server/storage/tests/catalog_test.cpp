// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/storage/catalog.hpp"

#include <sqlite3.h>

#include "test_util.hpp"

using namespace ombra::storage;
using namespace ombra::storage::testing;

int main() {
  TempDir dir;
  const std::string p = dir.path + "/catalog.db";
  {
    Catalog c(p);
    CHECK(c.next_segment_id() == 1 && c.next_segment_id() == 2);
    auto s = spec();
    c.create_collection(s);
    CHECK_THROWS(c.create_collection(s));  // duplicate name
    CollectionSpec back;
    CHECK(c.get_collection("docs", &back));
    CHECK(back.preset == s.preset && back.dimension == 8 && back.cluster_count == 4 &&
          back.key_tag == s.key_tag && back.security_level == 1 && back.api_version == 1);
    CHECK(back.filter_fields.size() == 2 && back.filter_fields[1].cardinality == 5);
    CHECK(!c.get_collection("nope", nullptr));

    c.add_segment("docs", {1, "s1", 2, 3, 99}, {"a", "b"});
    c.add_segment("docs", {1, "s1", 2, 3, 99}, {"a", "b"});  // idempotent
    CHECK(c.vector_ids("docs").size() == 2);
    CHECK_THROWS(c.add_segment("docs", {1, "s2", 2, 3, 99}, {"c", "a"}));  // dup id: rolled back
    CHECK(!c.has_segment("s2") && !c.id_exists("docs", "c"));
    CHECK_THROWS(c.add_segment("docs", {9, "s3", 1, 1, 1}, {"z"}));  // cluster FK

    c.tombstone("docs", {"a", "unknown"});
    c.tombstone("docs", {"a"});
    CHECK(c.tombstones("docs") == std::vector<std::string>{"a"});
    c.put_key_file("docs", "eval_mult", "k1.env");
    c.put_key_file("docs", "eval_mult", "k2.env");
    CHECK(c.key_files("docs").size() == 1 && c.key_files("docs")[0].second == "k2.env");
    sqlite3_exec(c.raw(), "UPDATE meta SET value=99 WHERE key='schema_version'", nullptr, nullptr,
                 nullptr);
  }
  CHECK_THROWS(Catalog c(p));
  {
    // Fresh catalog; tamper with the api_version instead.
    const std::string q = dir.path + "/c2.db";
    {
      Catalog c(q);
      sqlite3_exec(c.raw(), "UPDATE meta SET value=2 WHERE key='api_version'", nullptr, nullptr,
                   nullptr);
    }
    CHECK_THROWS(Catalog c(q));
  }

  // ---- Schema 2 and 1 upgrade in place to 3, rows kept; aliases then work. ----
  for (const int old : {1, 2}) {
    const std::string u = dir.path + "/up" + std::to_string(old) + ".db";
    {
      Catalog c(u);
      c.create_collection(spec());
      c.add_segment("docs", {1, "s1", 2, 3, 99}, {"a", "b"});
      auto e = [&](const char* sql) { sqlite3_exec(c.raw(), sql, nullptr, nullptr, nullptr); };
      e("DROP TABLE aliases");
      if (old == 1) e("DROP TABLE blobs");
      e(old == 1 ? "UPDATE meta SET value=1 WHERE key='schema_version'"
                 : "UPDATE meta SET value=2 WHERE key='schema_version'");
    }
    {
      Catalog c(u);  // upgrades
      CHECK(c.get_collection("docs", nullptr) && c.vector_ids("docs").size() == 2);
      CHECK(c.list_aliases().empty());
      CHECK(c.create_alias("live", "docs") == AliasResult::Ok);
      CHECK(c.get_alias("live", nullptr));
    }
    {
      Catalog c(u);  // second open: nothing to do, alias still there
      CHECK(c.list_aliases().size() == 1);
    }
  }

  // ---- Aliases ----
  {
    Catalog c(dir.path + "/alias.db");
    auto a = spec();
    c.create_collection(a);
    a.name = "docs.g1";
    c.create_collection(a);
    a.name = "docs.m1";
    c.create_collection(a);

    CHECK(c.create_alias("live", "docs") == AliasResult::Ok);
    CHECK(c.create_alias("live", "docs") == AliasResult::NameTaken);     // alias exists
    CHECK(c.create_alias("docs", "docs.g1") == AliasResult::NameTaken);  // a collection's name
    CHECK(c.create_alias("x.g7", "docs") == AliasResult::BadName);       // generation name
    CHECK(c.create_alias("x.gx", "docs") == AliasResult::Ok);            // not a generation name
    CHECK(c.create_alias("y", "nope") == AliasResult::TargetNotFound);
    CHECK_THROWS(c.create_collection([&] {
      auto b = spec();
      b.name = "live";  // collection with an alias's name
      return b;
    }()));

    AliasRow r;
    CHECK(c.get_alias("live", &r) && r.target == "docs" && r.previous.empty());

    // Compare-and-swap.
    AliasRow cur;
    CHECK(c.switch_alias("live", "docs.m1", "docs.g1", &cur) == AliasResult::Conflict);
    CHECK(cur.target == "docs");
    CHECK(c.switch_alias("live", "nope", "docs") == AliasResult::TargetNotFound);
    CHECK(c.switch_alias("none", "docs", "docs") == AliasResult::NotFound);
    CHECK(c.switch_alias("live", "docs.m1", "docs") == AliasResult::Ok);
    CHECK(c.get_alias("live", &r) && r.target == "docs.m1" && r.previous == "docs");
    // A retry of the same switch (lost answer) succeeds and changes nothing.
    CHECK(c.switch_alias("live", "docs.m1", "docs.m1") == AliasResult::Ok);
    CHECK(c.get_alias("live", &r) && r.previous == "docs");
    CHECK(c.switch_alias("live", "docs.m1", "docs") == AliasResult::Conflict);

    // Dropping a collection an alias points at is refused and changes nothing.
    std::vector<std::string> segs, keys;
    CHECK_THROWS(c.drop_collection("docs.m1", &segs, &keys));
    CHECK(c.get_collection("docs.m1", nullptr));
    // The previous target can be dropped (drain over); the alias forgets it.
    CHECK_THROWS(c.drop_collection("docs", &segs, &keys));  // x.gx still points at it
    CHECK(c.drop_alias("x.gx") == AliasResult::Ok);
    CHECK(c.drop_alias("x.gx") == AliasResult::NotFound);
    CHECK(c.drop_collection("docs", &segs, &keys));
    CHECK(c.get_alias("live", &r) && r.previous.empty());
    CHECK(c.list_aliases().size() == 1);
  }
  return 0;
}
