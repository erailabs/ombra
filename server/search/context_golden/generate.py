# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Writes the golden files of every preset in core/params/ckks_presets.json, made by the real
Python client (client/ombra/params.py make_context, openfhe-python):

  <preset>.bin             the crypto context as the client serializes it
  <preset>.evalmult_keys   structure template of an EvalMult key file
  <preset>.auto_keys       structure template of an automorphism key file

The server refuses a key file before OpenFHE (cereal) reads it unless every byte that cereal
interprets as structure (length prefixes, counts, version words, polymorphic names, moduli, ring
dimension, key tag length, map size, rotation indices, pointer ids) equals what the preset
requires. Only the coefficient words of the polynomials are free. The templates say which is which.

Learning. KEY_SAMPLES (8) independent key generations per preset are serialized by the real
client. A byte that is equal in all samples is structure; the key tag, which differs (32 hex
characters, written a few times per file), is replaced by a marker first. Bytes that differ must
lie in a polynomial value array: a run of exactly RING_DIM 8-byte words that follows its own
8-byte length prefix (RING_DIM as a little-endian uint64). The script stops if a differing byte is
anywhere else, or if an array is not exactly that shape. A whole 8-byte word counts as free as
soon as one of its bytes differs, so the high bytes of a coefficient (always zero or small,
because a coefficient is below a modulus of at most 60 bits) are free too.

Why learning cannot admit a structure byte. Structure does not depend on the secret key, so a
structure byte is equal in all samples and is never marked free. The only error learning can make
is the other way round: a coefficient word that happens to be equal in all samples is kept as
structure. A coefficient is uniform below a modulus q of at least 2^40, so a word is equal in all
KEY_SAMPLES samples with probability at most q^-(KEY_SAMPLES-1) <= 2^-280. A file has fewer than
2^23 words, so the chance that any word is misjudged is below 2^-257. Such an error would break
the shape check above (this script stops) or make the server refuse a valid key; it can never
make the server admit an invalid one.

An automorphism file of n keys is HEAD (the first key, with the crypto context copy), then n-1
blocks MID (all later keys have the same shape). Three kinds of field change with the position in
the file: the map size (HEAD, uint64), the rotation index of each key (uint32; the keys are stored
in ascending order, so the j-th of n has N/2^(n-1-j)+1) and the id cereal gives to each shared
pointer (one more for each MID block). The server checks them against what n keys require. This
script verifies the rule on files of 1, 2 and the maximum number of keys.

The server embeds these files at build time (server/search/src/context_preflight.hpp). Run this
again when the presets, the client or the OpenFHE version change; the C++ test
search_context_preflight and tests/test_openfhe_key_templates.py fail until you do.

    python server/search/context_golden/generate.py            # all presets
    python server/search/context_golden/generate.py m0_depth2  # one preset

Template file format (all integers little-endian), version 1:
  "OKT1"  u8 kind (0 EvalMult, 1 automorphism)  u32 ring_dim  u32 max_keys  u32 block_count
  per block (HEAD, then MID for kind 1): u32 item_count, then items:
    0 LIT   u32 length, bytes     bytes that must be equal
    1 TAG   -                     32 lowercase hex characters, equal in the whole file
    2 ARR   u32 words             coefficient words, any value
    3 COUNT -                     uint64 number of keys in the file (HEAD only)
    4 INDEX -                     uint32 rotation index of this key
    5 SEQID u32 base              uint32 = base + (block number - 1) (MID only)
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "client"))

KEY_SAMPLES = 8
MAGIC = b"OKT1"
LIT, TAG, ARR, COUNT, INDEX, SEQID = range(6)
TAG_LEN = 32
MARK = b"T" * TAG_LEN  # replaces the key tag in a sample; 'T' is not a hex digit


def read_sample(raw: bytes, tag: str) -> tuple[np.ndarray, list[int]]:
    """The bytes of a key file with the tag replaced by MARK, and where the tag was."""
    b = bytearray(raw)
    t = tag.encode()
    assert len(t) == TAG_LEN and MARK not in raw
    pos, i = [], b.find(t)
    while i >= 0:
        pos.append(i)
        b[i : i + TAG_LEN] = MARK
        i = b.find(t, i + TAG_LEN)
    return np.frombuffer(bytes(b), dtype=np.uint8), pos


def learn_arrays(samples: list[np.ndarray], n: int) -> list[int]:
    """Start offsets of the polynomial value arrays: the places where the samples differ.

    Stops unless every differing byte is inside an array of exactly n words that follows its
    length prefix (n as uint64) and every array is cut in the right place.
    """
    base = samples[0]
    diff = np.zeros(len(base), dtype=bool)
    for s in samples[1:]:
        assert len(s) == len(base)
        diff |= s != base
    idx = np.flatnonzero(diff)
    assert len(idx), "samples do not differ: not independent key generations"
    cut = np.flatnonzero(np.diff(idx) >= 8)  # inside an array, differing bytes are < 8 apart
    first = np.concatenate([[idx[0]], idx[cut + 1]])
    last = np.concatenate([idx[cut], [idx[-1]]]) + 1
    prefix = struct.pack("<Q", n)
    starts = []
    for a, b in zip(first.tolist(), last.tolist(), strict=True):
        cand = [s for s in range(max(a - 7, 8), a + 1) if base[s - 8 : s].tobytes() == prefix]
        assert len(cand) == 1, f"no unique array start for the difference at {a}"
        s = cand[0]
        assert s + 8 * n - 8 < b <= s + 8 * n, f"array at {s} is not {n} words (ends {b})"
        starts.append(s)
    assert all(b - a >= 8 * n for a, b in zip(starts, starts[1:], strict=False))
    return starts


def items_for(
    base: np.ndarray,
    lo: int,
    hi: int,
    arrays: list[int],
    tags: list[int],
    n: int,
    fields: dict[int, int],
) -> list[tuple]:
    """Items for base[lo:hi]. `fields` maps an offset to COUNT/INDEX/SEQID (fixed widths)."""
    width = {COUNT: 8, INDEX: 4, SEQID: 4}
    spans = [(s, s + 8 * n, (ARR, n)) for s in arrays if lo <= s < hi]
    spans += [(p, p + TAG_LEN, (TAG,)) for p in tags if lo <= p < hi]
    for off, kind in fields.items():
        if lo <= off < hi:
            value = int.from_bytes(base[off : off + width[kind]].tobytes(), "little")
            spans.append((off, off + width[kind], (kind, value)))
    spans.sort()
    out, cur = [], lo
    for a, b, item in spans:
        assert a >= cur and b <= hi, "overlapping spans"
        if a > cur:
            out.append((LIT, base[cur:a].tobytes()))
        out.append(item)
        cur = b
    if cur < hi:
        out.append((LIT, base[cur:hi].tobytes()))
    return out


def encode_items(items: list[tuple]) -> bytes:
    out = struct.pack("<I", len(items))
    for it in items:
        out += bytes([it[0]])
        if it[0] == LIT:
            out += struct.pack("<I", len(it[1])) + it[1]
        elif it[0] in (ARR, SEQID):
            out += struct.pack("<I", it[1])
    return out


def header(kind: int, ring: int, max_keys: int, blocks: int) -> bytes:
    return MAGIC + bytes([kind]) + struct.pack("<III", ring, max_keys, blocks)


def expected_index(ring: int, n_keys: int, j: int) -> int:
    return ring // (1 << (n_keys - 1 - j)) + 1


def fresh_keys(name: str, n_auto: int, with_mult: bool = False):
    """One key generation by the client: (EvalMult file or None, automorphism file, tag)."""
    import openfhe as ofhe
    from ombra.params import make_context

    _, cc = make_context(name)
    ring = cc.GetRingDimension()
    kp = cc.KeyGen()
    tag = kp.publicKey.GetKeyTag()
    mult = None
    if with_mult:
        cc.EvalMultKeyGen(kp.secretKey)
        mult = ofhe.SerializeEvalMultKeyString(ofhe.BINARY, tag)
    cc.EvalAutomorphismKeyGen(kp.secretKey, [ring // (1 << k) + 1 for k in range(n_auto)])
    return mult, ofhe.SerializeEvalAutomorphismKeyString(ofhe.BINARY, tag), tag


def build_templates(name: str) -> tuple[bytes, bytes, dict]:
    from ombra.params import make_context

    _, cc = make_context(name)
    ring = cc.GetRingDimension()
    max_keys = ring.bit_length() - 1

    mult_s, auto_s = [], []
    for _ in range(KEY_SAMPLES):
        mult, auto, tag = fresh_keys(name, max_keys, with_mult=True)
        mult_s.append(read_sample(mult, tag))
        auto_s.append(read_sample(auto, tag))
    stats = {"ring": ring, "max_keys": max_keys}

    # ---- EvalMult: one key, the whole file is one block ----
    assert all(p == mult_s[0][1] for _, p in mult_s)
    arrays = learn_arrays([s for s, _ in mult_s], ring)
    base, tags = mult_s[0]
    mult_items = items_for(base, 0, len(base), arrays, tags, ring, {})
    mult_tmpl = header(0, ring, 1, 1) + encode_items(mult_items)
    stats["mult_words"] = len(arrays) * ring

    # ---- Automorphism: HEAD (first key) + (n-1) x MID ----
    assert all(p == auto_s[0][1] for _, p in auto_s)
    _, one, one_tag = fresh_keys(name, 1)
    _, two, two_tag = fresh_keys(name, 2)
    f1, _ = read_sample(one, one_tag)
    f2, _ = read_sample(two, two_tag)
    base, tags = auto_s[0]
    head_len = len(f1)
    mid_len, rem = divmod(len(base) - head_len, max_keys - 1)
    assert rem == 0, "the file is not HEAD + (n-1) equal blocks"
    arrays = learn_arrays([s for s, _ in auto_s], ring)
    stats["auto_words"] = len(arrays) * ring

    # Count and first index: where HEAD differs between the files of 1, 2 and max_keys keys.
    arr_free = np.zeros(head_len, dtype=bool)
    for s in arrays:
        if s < head_len:
            arr_free[s : s + 8 * ring] = True
    d = np.flatnonzero((f1 != base[:head_len]) | (f2[:head_len] != base[:head_len]))
    d = d[~arr_free[d]]
    count_off = int(d[0])
    index_off = count_off + 8
    assert d[-1] < index_off + 4, "unexpected difference in HEAD"
    for f, n_keys in ((f1, 1), (f2, 2), (base, max_keys)):
        assert int.from_bytes(f[count_off : count_off + 8].tobytes(), "little") == n_keys
        got = int.from_bytes(f[index_off : index_off + 4].tobytes(), "little")
        assert got == expected_index(ring, n_keys, 0), "index of the first key"
    for s in (f1, f2):  # HEAD is the same in all files except the two fields
        same = s[:head_len] == base[:head_len]
        same[arr_free] = True
        same[count_off : index_off + 4] = True
        assert same.all(), "HEAD differs between key counts"

    # MID: block j (1-based) starts at head_len + (j-1)*mid_len. The index is at +0 (uint32), the
    # shared pointer id at +8 (uint32, one more per block), the key tag once.
    def mid(arr, j):
        return arr[head_len + (j - 1) * mid_len : head_len + j * mid_len]

    free_mid = np.zeros(mid_len, dtype=bool)
    for s in arrays:
        if head_len <= s < head_len + (max_keys - 1) * mid_len:
            free_mid[(s - head_len) % mid_len : (s - head_len) % mid_len + 8 * ring] = True
    tag_mid = [p - head_len for p in tags if p >= head_len and (p - head_len) < mid_len]
    assert len(tag_mid) == 1
    ref = mid(base, 1)
    id1 = int.from_bytes(ref[8:12].tobytes(), "little")
    assert id1 >> 31 == 1
    for sample, _ in auto_s:
        for j in range(1, max_keys):
            blk = mid(sample, j)
            same = blk == ref
            same[free_mid] = True
            same[0:4] = same[8:12] = True  # the two fields
            assert same.all(), f"block {j} differs from block 1 outside the known fields"
            assert int.from_bytes(blk[0:4].tobytes(), "little") == expected_index(
                ring, max_keys, j
            )
            assert int.from_bytes(blk[8:12].tobytes(), "little") == id1 + j - 1
    assert len(f2) == head_len + mid_len
    blk = f2[head_len:]
    same = blk == ref
    same[free_mid] = True
    same[0:4] = same[8:12] = True
    assert same.all()
    assert int.from_bytes(blk[0:4].tobytes(), "little") == expected_index(ring, 2, 1)
    assert int.from_bytes(blk[8:12].tobytes(), "little") == id1

    head_items = items_for(
        base,
        0,
        head_len,
        [s for s in arrays if s < head_len],
        [p for p in tags if p < head_len],
        ring,
        {count_off: COUNT, index_off: INDEX},
    )
    shifted = [s - head_len for s in arrays if head_len <= s < head_len + mid_len]
    mid_items = items_for(ref, 0, mid_len, shifted, tag_mid, ring, {0: INDEX, 8: SEQID})
    auto_tmpl = header(1, ring, max_keys, 2) + encode_items(head_items) + encode_items(mid_items)
    stats["head_bytes"], stats["mid_bytes"] = head_len, mid_len
    return mult_tmpl, auto_tmpl, stats


def main() -> None:
    import openfhe as ofhe
    from ombra.params import load_presets, make_context

    presets = json.loads((ROOT / "core/params/ckks_presets.json").read_text())["presets"]
    for name in sys.argv[1:] or list(load_presets()):
        _, cc = make_context(name)
        (HERE / f"{name}.bin").write_bytes(ofhe.Serialize(cc, ofhe.BINARY))
        mult, auto, stats = build_templates(name)
        (HERE / f"{name}.evalmult_keys").write_bytes(mult)
        (HERE / f"{name}.auto_keys").write_bytes(auto)
        bits = min(presets[name]["scaling_mod_size"], presets[name]["first_mod_size"])
        words = stats["mult_words"] + stats["auto_words"]
        log_p = math.log2(words) - bits * (KEY_SAMPLES - 1)
        print("wrote", name, stats, f"P(a coefficient word kept as structure) < 2^{log_p:.0f}")


if __name__ == "__main__":
    main()
