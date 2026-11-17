// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// SQLite catalog (schema_version 3, api_version 1; schema 1 and 2 catalogs are migrated in place:
// version 2 added the blob table, version 3 the alias table). journal_mode=WAL, synchronous=FULL,
// foreign_keys=ON. Opening a catalog with another stored api_version or schema_version is refused.
#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

struct sqlite3;

namespace ombra::storage {

inline constexpr int kCatalogSchemaVersion = 3;
inline constexpr int kCatalogApiVersion = 1;

struct FilterField {
  std::uint32_t field_id = 0;
  std::uint32_t cardinality = 0;
};

// Mirrors proto CollectionSpec (proto/ombra/v1/ombra.proto).
struct CollectionSpec {
  std::uint32_t api_version = 1;
  std::string name;
  std::string preset;
  std::uint32_t dimension = 0;
  std::uint32_t security_level = 0;  // proto SecurityLevel value
  std::vector<FilterField> filter_fields;
  std::string key_tag;
  std::uint32_t cluster_count = 0;
};

// An alias is a name that points at one collection. `previous` is the target before
// the last switch (empty if never switched); a key scoped to the alias may use both. Alias names
// share the namespace of collection names.
struct AliasRow {
  std::string name, target, previous;
  std::int64_t switched_at = 0;  // unix seconds of the last switch (or of the creation)
};

enum class AliasResult {
  Ok,
  NotFound,        // no alias with this name
  NameTaken,       // a collection or an alias already has the name
  BadName,         // the name looks like a generation name (<base>.g<n>)
  TargetNotFound,  // the target collection does not exist
  Conflict,        // compare-and-swap: the alias does not point at the expected collection
};

// True for names like "docs.g12": the form that re-clustering gives to a new generation.
bool is_generation_name(const std::string& name);

// Thrown by drop_collection when an alias still points at the collection.
struct AliasTargetError : std::runtime_error {
  AliasTargetError() : std::runtime_error("catalog: an alias points at this collection") {}
};

struct SegmentRow {
  std::uint32_t cluster = 0;
  std::string file;
  std::uint32_t n_vectors = 0, n_columns = 0, crc = 0;
};

// Counts per stored block (segment); no ids. n_vectors includes tombstoned slots.
struct BlockStat {
  std::string segment;
  std::uint32_t cluster = 0;
  std::uint64_t n_vectors = 0, n_deleted = 0;
};

class Catalog {
 public:
  explicit Catalog(const std::string& path);
  ~Catalog();
  Catalog(const Catalog&) = delete;
  Catalog& operator=(const Catalog&) = delete;

  void create_collection(const CollectionSpec& spec);  // also creates the cluster rows
  bool get_collection(const std::string& name, CollectionSpec* out) const;
  std::vector<std::string> list_collections() const;
  // Aliases. Each call is one transaction. create_alias refuses a name that is a collection or an
  // alias or looks like a generation name. switch_alias is a compare-and-swap: it moves the alias
  // to `new_target` only if it points at `expected_target` now (else Conflict, and *current, if
  // given, holds the alias as it is). The old target becomes `previous`. A repeat of a switch that
  // was applied (the alias is at `new_target` and `previous` is `expected_target`), or a switch to
  // the current target, is a no-op that succeeds (a retry after a lost answer).
  AliasResult create_alias(const std::string& name, const std::string& target);
  AliasResult switch_alias(const std::string& name, const std::string& new_target,
                           const std::string& expected_target, AliasRow* current = nullptr);
  AliasResult drop_alias(const std::string& name);
  bool get_alias(const std::string& name, AliasRow* out) const;
  std::vector<AliasRow> list_aliases() const;

  // One transaction: removes the collection and every row that belongs to it (tombstones, vectors,
  // segments, clusters, key file rows). Returns the segment and key file names that were
  // registered, for the caller to unlink after the commit. False if the collection is unknown.
  // Throws AliasTargetError, and removes nothing, while an alias points at it. An alias whose
  // `previous` is this collection forgets it (the drain window is over).
  bool drop_collection(const std::string& name, std::vector<std::string>* segment_files,
                       std::vector<std::string>* key_files,
                       std::vector<std::string>* blob_files = nullptr);

  std::uint64_t next_segment_id();  // durable counter
  bool has_segment(const std::string& file) const;
  std::vector<SegmentRow> segments(const std::string& collection) const;
  std::vector<std::string> all_segment_files() const;

  // One transaction: the segment row plus one vectors row per id. Fails (and rolls back) on a
  // duplicate id. Idempotent when the segment is already registered.
  void add_segment(const std::string& collection, const SegmentRow& seg,
                   const std::vector<std::string>& ids);
  bool id_exists(const std::string& collection, const std::string& id) const;
  // The segment file that holds `id` (tombstoned or not); false if the id is unknown.
  bool segment_of_id(const std::string& collection, const std::string& id, std::string* file) const;
  // Tombstone the ids that exist; unknown ids are ignored; idempotent.
  void tombstone(const std::string& collection, const std::vector<std::string>& ids);
  std::vector<std::string> tombstones(const std::string& collection) const;
  std::vector<std::string> vector_ids(const std::string& collection) const;
  // True if the id exists, is stored in a cluster other than `excluded_cluster` and is not yet
  // tombstoned. (The server passes spec.cluster_count, the reserved id of the center blocks, so a
  // delete can never reach a cluster center.)
  bool is_live_in_other_cluster(const std::string& collection, const std::string& id,
                                std::uint32_t excluded_cluster) const;

  // One row per block of the collection, ordered by segment name.
  std::vector<BlockStat> block_stats(const std::string& collection) const;
  // Segment row by file name (false if the collection has no such segment).
  bool segment_row(const std::string& collection, const std::string& file, SegmentRow* out) const;
  // Ids of the vectors stored in `file` that are not tombstoned.
  std::vector<std::string> live_ids(const std::string& collection, const std::string& file) const;
  // One transaction: removes the segment rows `files` with their vectors and tombstone rows.
  void drop_segments(const std::string& collection, const std::vector<std::string>& files);
  // One transaction: adds `neu`, moves the live vectors rows of all `old_files` to it, removes the
  // dead ones (vectors and tombstones) and the old segment rows. Throws if the union of the live
  // ids of old_files differs from `live_ids` (as a set) or a file is unknown; rolls back then.
  void replace_segments(const std::string& collection, const std::vector<std::string>& old_files,
                        const SegmentRow& neu, const std::vector<std::string>& live_ids);

  void put_key_file(const std::string& collection, const std::string& kind,
                    const std::string& file);
  // One transaction for several kinds (kind, file).
  void put_key_files(const std::string& collection,
                     const std::vector<std::pair<std::string, std::string>>& kind_file);
  std::vector<std::pair<std::string, std::string>> key_files(const std::string& collection) const;
  std::vector<std::string> all_key_files() const;

  // Blob rows, keyed by (collection, kind, id). put_blob is one transaction that inserts or
  // replaces the row; returns true and sets *old_file if a row was replaced. delete_blobs removes
  // the rows that exist (one transaction) and returns their files. Ids are opaque bytes.
  bool put_blob(const std::string& collection, std::uint32_t kind, const std::string& id,
                const std::string& file, std::uint64_t size, std::string* old_file);
  bool get_blob(const std::string& collection, std::uint32_t kind, const std::string& id,
                std::string* file, std::uint64_t* size) const;
  std::vector<std::string> delete_blobs(const std::string& collection, std::uint32_t kind,
                                        const std::vector<std::string>& ids);
  std::vector<std::string> all_blob_files() const;
  // Number and total payload bytes of the blobs of a collection (for quotas).
  void blob_totals(const std::string& collection, std::uint64_t* count, std::uint64_t* bytes) const;

  sqlite3* raw() const { return db_; }  // for tests that tamper with versions

 private:
  sqlite3* db_ = nullptr;
};

}  // namespace ombra::storage
