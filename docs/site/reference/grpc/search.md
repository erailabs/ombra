# Search

## Search

```proto
rpc Search(stream SearchRequest) returns (stream SearchResponse)
```

Flow:

1. The client sends a `SearchStart`. The server checks `filter_leaves` against the spec. The query is expanded once.
2. The server answers with `SearchCenterScores`: one score ciphertext per block of cluster centers. The client decrypts them and picks clusters.
3. The client sends `SearchChosen` with real and decoy cluster ids mixed. The server answers with `SearchScores` messages, the last with `last` set. Each slot holds `score + lambda * c`, where `c` counts the filter fields a vector fails. The client may send further `SearchChosen` messages on the same stream; each reuses the expansion.
4. The client half-closes to end the stream.

**SearchRequest** (one per message; one of)

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `start` | `SearchStart` | first message | Opens the search. |
| `chosen` | `SearchChosen` | after the centers | Clusters to score. |

**SearchStart**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `collection` | string | yes | Real collection name (an alias is not resolved). |
| `key_tag` | string | yes | Checked against the collection's `key_tag`. |
| `query` | bytes | yes | One versioned envelope with `dimension + sum(cardinality)` entries. |
| `filter_leaves` | uint32 | yes | `sum(cardinality)` of the filter fields. The server checks it against the spec. |

**SearchChosen**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `cluster_ids` | repeated uint32 | yes | Real and decoy cluster ids, at most `LIMIT_MAX_CLUSTERS_PER_ROUND` (4096). |

**SearchResponse** (one per message; one of)

| Field | Type | Description |
| --- | --- | --- |
| `centers` | `SearchCenterScores` | Round 1 answer. |
| `scores` | `SearchScores` | Round 2 answers. |
| `error` | `Error` | Set on failure. |

**SearchCenterScores**

| Field | Type | Description |
| --- | --- | --- |
| `score_blocks` | repeated bytes | Envelopes, each decrypts to one score per slot. One per block of cluster centers. |
| `center_count` | uint32 | Number of centers. |

**SearchScores**

| Field | Type | Description |
| --- | --- | --- |
| `cluster_id` | uint32 | The cluster these scores belong to. |
| `score_blocks` | repeated bytes | Envelopes. Slot i holds `score + lambda * c`. |
| `ids` | repeated bytes | Ids in slot order within the cluster, tombstoned slots included. |
| `last` | bool | True on the last message of the round. |
| `block_sizes` | repeated uint32 | The occupied slots of each score block, in block order, summing to the number of ids. Always set, also by a router. |

Errors: `ERROR_COLLECTION_NOT_FOUND`, `ERROR_KEYS_MISSING`, `ERROR_KEY_TAG_MISMATCH`, `ERROR_PRESET_MISMATCH`, `ERROR_INVALID_FILTER_LAYOUT` (`filter_leaves` does not match the spec), `ERROR_UNSUPPORTED_VERSION`, `ERROR_INVALID_ARGUMENT`, `ERROR_SIZE_LIMIT`, `ERROR_BAD_SEQUENCE`, `ERROR_UNAVAILABLE` (router only; the client opens a new stream and repeats the same requests, with the same decoys).

Notes:

- The client drops tombstoned slots from `ids`.
- `block_sizes` makes each answer describe itself, because blocks may change between answers and a router's nodes may order blocks differently.
- Running searches finish on the old collection when an alias moves. See [Aliases](aliases.md).
