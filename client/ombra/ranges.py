# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Integer range filters on top of the equality filter columns (decision 0030).

A range field is stored as ordinary filter fields; the server never sees a difference.

* `buckets` (option A): one field of 64 values. The client keeps the bucket edges in an encrypted
  schema blob. A range becomes the list of the buckets it overlaps; the two edge buckets may let
  in values outside the range, which the client removes from the raw integer in the record.
* `two_digit` (option D): two 64-value fields, coarse = v // 64 and fine = v % 64, for the domain
  0..4095. The query weighs coarse buckets inside the range 2, the two edge coarse buckets 1 and
  the fine values of the edges 1. A vector in range scores 2 or 3 units for the field, one outside
  at most 1 (a two-sided range over several coarse buckets can let edge vectors reach 2 through the
  shared fine weights). The decoder accepts totals from F to F + m for m such fields in the filter;
  the exact check on the client removes the rest.

Schema blob (version 1): magic b"OSCH", u16 version, u32 length, JSON. Sealed under the reserved
id `SCHEMA_ID` with the collection's document codec, never stored in plaintext.
"""

from __future__ import annotations

import json
import struct
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ombra import presets
from ombra.probe import RESERVED_PREFIX

SCHEMA_ID = RESERVED_PREFIX + b"schema"
BUCKETS = presets.MAX_FILTER_CARDINALITY  # 64 buckets; a range field always has this many values
DIGIT = 64
D_DOMAIN = DIGIT * DIGIT  # 4096
FETCH_FACTOR = 4  # records fetched per query = FETCH_FACTOR * rerank_factor * k (fixed)
_MAGIC = b"OSCH"
_VERSION = 1
_HEAD = struct.Struct("<4sHxxI")  # 12 bytes
_BIG = 1 << 62

KINDS = ("buckets", "two_digit")


def equal_width_edges(lo: int, hi: int, buckets: int = BUCKETS) -> list[int]:
    """Interior cut points for `buckets` buckets of equal width over [lo, hi]."""
    n = max(1, min(buckets, hi - lo + 1))
    cuts = {lo + (hi - lo + 1) * i // n for i in range(1, n)}
    return sorted(c for c in cuts if lo < c <= hi)


def quantile_edges(values: Sequence[int] | np.ndarray, buckets: int = BUCKETS) -> list[int]:
    """Interior cut points so that each bucket holds about the same number of `values`."""
    v = np.sort(np.asarray(values, dtype=np.int64))
    if len(v) == 0:
        raise ValueError("need at least one value")
    cuts = {int(v[len(v) * i // buckets]) for i in range(1, buckets)}
    return sorted(c for c in cuts if c > int(v[0]))


def parse_bounds(want: Any) -> tuple[int, int]:
    """(lo, hi) inclusive from an integer (equality) or {"gte"/"gt"/"lte"/"lt": int}."""
    if isinstance(want, int | np.integer) and not isinstance(want, bool):
        return int(want), int(want)
    if not isinstance(want, Mapping) or not want:
        raise ValueError("a range filter is an integer or a dict with gte, gt, lte or lt")
    lo, hi = -_BIG, _BIG
    for key, val in want.items():
        if key not in ("gte", "gt", "lte", "lt") or not isinstance(val, int | np.integer):
            raise ValueError("a range filter takes integer bounds gte, gt, lte, lt only")
        val = int(val)
        if key == "gte":
            lo = max(lo, val)
        elif key == "gt":
            lo = max(lo, val + 1)
        elif key == "lte":
            hi = min(hi, val)
        else:
            hi = min(hi, val - 1)
    if lo > hi:
        raise ValueError("the range is empty")
    return lo, hi


@dataclass(frozen=True)
class RangeField:
    name: str
    kind: str
    index: int  # first filter field index; two_digit uses index (coarse) and index + 1 (fine)
    edges: tuple[int, ...] = ()  # buckets: interior cut points, strictly ascending, at most 63

    @property
    def n_fields(self) -> int:
        return 2 if self.kind == "two_digit" else 1

    def bucket_of(self, v: int) -> int:
        return bisect_right(self.edges, v)

    def check_value(self, v: Any) -> int:
        if not isinstance(v, int | np.integer) or isinstance(v, bool):
            raise ValueError(f"metadata field {self.name!r} must be an integer")
        v = int(v)
        if self.kind == "two_digit" and not 0 <= v < D_DOMAIN:
            raise ValueError(f"metadata field {self.name!r} must be in [0, {D_DOMAIN})")
        return v

    def buckets(self, v: int) -> list[int]:
        if self.kind == "two_digit":
            return [v // DIGIT, v % DIGIT]
        return [self.bucket_of(v)]


@dataclass(frozen=True)
class RangePlan:
    """What one query needs besides the equality filter."""

    weights: dict[int, np.ndarray]  # field index -> unit weight per value
    planner: dict[int, list[int]]  # field index -> values, for the cluster statistics
    checks: tuple[tuple[str, int, int], ...]  # (name, lo, hi) exact checks on the raw value
    d_fields: int  # two_digit fields in the filter

    def accept(self, n_fields: int) -> tuple[int, int]:
        """Allowed decoded totals: F for equality and bucket fields, F..F+m with m digit pairs."""
        return n_fields, n_fields + self.d_fields

    def exact(self, meta: Mapping[str, Any]) -> bool:
        for name, lo, hi in self.checks:
            v = meta.get(name)
            if not isinstance(v, int | np.integer) or not lo <= int(v) <= hi:
                return False
        return True


@dataclass(frozen=True)
class RangeSchema:
    fields: tuple[RangeField, ...]

    @classmethod
    def from_spec(
        cls, spec: Mapping[str, Any], first_index: int, taken: Sequence[str] = ()
    ) -> RangeSchema:
        out, idx = [], first_index
        for name, how in spec.items():
            if name in taken:
                raise ValueError(f"field name {name!r} is used twice")
            if isinstance(how, Mapping):
                kind = how.get("kind", "buckets")
                edges = how.get("edges", ())
            else:
                kind, edges = "buckets", how
            if kind not in KINDS:
                raise ValueError(f"range field kind must be one of {list(KINDS)}")
            edges = tuple(int(e) for e in edges)
            if kind == "buckets":
                ascending = all(a < b for a, b in zip(edges, edges[1:], strict=False))
                if len(edges) > BUCKETS - 1 or not ascending:
                    raise ValueError(
                        f"edges of {name!r}: strictly ascending, at most {BUCKETS - 1} cut points"
                    )
            elif edges:
                raise ValueError("a two_digit range field takes no edges")
            f = RangeField(name, kind, idx, edges)
            out.append(f)
            idx += f.n_fields
        return cls(tuple(out))

    @property
    def cards(self) -> tuple[int, ...]:
        return tuple(BUCKETS for f in self.fields for _ in range(f.n_fields))

    @property
    def n_fields(self) -> int:
        return sum(f.n_fields for f in self.fields)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def get(self, name: str) -> RangeField | None:
        return next((f for f in self.fields if f.name == name), None)

    def check_headroom(self, derived: presets.Derived, n_total_fields: int) -> None:
        """A digit pair counts as 3 units toward F_max (2 fields plus 1 for the total of 3)."""
        units = n_total_fields + sum(1 for f in self.fields if f.kind == "two_digit")
        fmax = presets.filter_field_limit(derived)
        if units > fmax:
            raise presets.ParamsError(
                f"{units} filter units (a two_digit field counts 3); preset {derived.preset} "
                f"allows {fmax}"
            )

    def bucket_values(self, metadata: Sequence[Mapping[str, Any]] | None, n: int) -> np.ndarray:
        if metadata is None or len(metadata) != n:
            raise ValueError("a collection with range fields needs one metadata dict per vector")
        out = np.zeros((n, self.n_fields), dtype=np.int64)
        base = self.fields[0].index if self.fields else 0
        for i, m in enumerate(metadata):
            for f in self.fields:
                if f.name not in m:
                    raise ValueError(f"metadata field {f.name!r} is required for every vector")
                c = f.index - base
                out[i, c : c + f.n_fields] = f.buckets(f.check_value(m[f.name]))
        return out

    def plan(self, wants: Mapping[str, Any]) -> RangePlan:
        weights: dict[int, np.ndarray] = {}
        planner: dict[int, list[int]] = {}
        checks, d_fields = [], 0
        for name, want in wants.items():
            f = self.get(name)
            if f is None:
                raise ValueError(f"{name!r} is not a range field")
            lo, hi = parse_bounds(want)
            if f.kind == "buckets":
                b_lo, b_hi = f.bucket_of(lo), f.bucket_of(hi)
                w = np.zeros(BUCKETS)
                w[b_lo : b_hi + 1] = 1.0
                weights[f.index] = w
                planner[f.index] = list(range(b_lo, b_hi + 1))
            else:
                lo, hi = max(lo, 0), min(hi, D_DOMAIN - 1)
                if lo > hi:
                    raise ValueError("the range is outside the domain 0..4095")
                weights[f.index], weights[f.index + 1] = _digit_weights(lo, hi)
                planner[f.index] = list(range(lo // DIGIT, hi // DIGIT + 1))
                d_fields += 1
            checks.append((name, lo, hi))
        return RangePlan(weights, planner, tuple(checks), d_fields)

    # ---- persistence ---------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        body = json.dumps(
            [
                {"name": f.name, "kind": f.kind, "index": f.index, "edges": list(f.edges)}
                for f in self.fields
            ],
            separators=(",", ":"),
        ).encode()
        body += b" " * (-len(body) % 4)  # a record blob holds whole float32 values
        return _HEAD.pack(_MAGIC, _VERSION, len(body)) + body

    @classmethod
    def from_bytes(cls, raw: bytes) -> RangeSchema:
        magic, version, n = _HEAD.unpack_from(raw)
        if magic != _MAGIC or version != _VERSION:
            raise ValueError("schema blob has an unknown format or version")
        items = json.loads(raw[_HEAD.size : _HEAD.size + n])
        return cls(
            tuple(
                RangeField(i["name"], i["kind"], int(i["index"]), tuple(int(e) for e in i["edges"]))
                for i in items
            )
        )

    def seal(self, codec: Any) -> bytes:
        return codec.seal_record(SCHEMA_ID, self.to_bytes(), {})

    @classmethod
    def unseal(cls, codec: Any, blob: bytes) -> RangeSchema:
        return cls.from_bytes(codec.open_record(SCHEMA_ID, blob).vector)


def _digit_weights(lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Unit weights (coarse, fine) for the range [lo, hi] inside 0..4095."""
    c_lo, c_hi = lo // DIGIT, hi // DIGIT
    coarse, fine = np.zeros(DIGIT), np.zeros(DIGIT)
    coarse[c_lo : c_hi + 1] = 2.0
    coarse[c_lo] = 1.0
    coarse[c_hi] = 1.0
    if c_lo == c_hi:
        fine[lo % DIGIT : hi % DIGIT + 1] = 1.0
    else:
        fine[lo % DIGIT :] = 1.0
        fine[: hi % DIGIT + 1] = 1.0
    return coarse, fine
