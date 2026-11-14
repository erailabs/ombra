# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Writes the golden files of every preset in core/params/ckks_presets.json, made by the real
Python client (client/ombra/params.py make_context, openfhe-python):

  <preset>.bin               the crypto context as the client serializes it
  <preset>.evalmult_header   template and mask of the first HEADER_LEN bytes of an EvalMult key file
  <preset>.auto_header       the same for an automorphism key file

Every evaluation key file starts with its key tag and a full copy of the crypto context, which
OpenFHE parses; the first HEADER_LEN bytes carry nothing that depends on the secret key except the
key tag (written twice). A header file is HEADER_LEN template bytes (the tag replaced by 32 'T') and
HEADER_LEN mask bytes (1: the byte may differ, which are the key count and first rotation index of
an automorphism file; found by comparing files made for 2, 3 and 384 vectors).

The server embeds these files at build time (server/search/src/context_preflight.hpp). Run this
again when the presets or the OpenFHE version change; tests/test_openfhe_context_canonical.py and
the C++ test search_context_preflight fail until you do.

    python server/search/context_golden/generate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "client"))

HEADER_LEN = 840  # before the first polynomial of the first key (offset 852 for both presets)
SAMPLE_DIMENSIONS = (2, 3, 384)


def key_headers(name: str) -> tuple[bytes, bytes]:
    """(evalmult_header, auto_header) file contents for one preset."""
    from ombra import query as oq
    from ombra.params import make_context

    import openfhe as ofhe

    _, cc = make_context(name)
    kp = cc.KeyGen()
    tag = kp.publicKey.GetKeyTag()
    assert len(tag) == 32
    cc.EvalMultKeyGen(kp.secretKey)

    def head(raw: bytes) -> bytes:
        part = raw[:HEADER_LEN]
        assert part.count(tag.encode()) == 2  # a file of several keys repeats the tag later
        return part.replace(tag.encode(), b"T" * 32)

    mult = head(ofhe.SerializeEvalMultKeyString(ofhe.BINARY, tag))
    autos = []
    for d in SAMPLE_DIMENSIONS:
        cc.EvalAutomorphismKeyGen(kp.secretKey, oq.automorphism_indices(cc.GetRingDimension(), d))
        autos.append(head(ofhe.SerializeEvalAutomorphismKeyString(ofhe.BINARY, tag)))
    mask = bytes(int(len({a[i] for a in autos}) > 1) for i in range(HEADER_LEN))
    return mult + bytes(HEADER_LEN), autos[0] + mask


def main() -> None:
    import openfhe as ofhe
    from ombra.params import load_presets, make_context

    for name in load_presets():
        _, cc = make_context(name)
        (HERE / f"{name}.bin").write_bytes(ofhe.Serialize(cc, ofhe.BINARY))
        mult, auto = key_headers(name)
        (HERE / f"{name}.evalmult_header").write_bytes(mult)
        (HERE / f"{name}.auto_header").write_bytes(auto)
        print("wrote", name)


if __name__ == "__main__":
    main()
