# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Writes <preset>.bin here: the crypto context of every preset in core/params/ckks_presets.json,
serialized by the real Python client (client/ombra/params.py make_context, openfhe-python).

The server embeds these files at build time and accepts an uploaded context only if its bytes equal
one of them or the server's own serialization (server/search/src/context_preflight.hpp). Run this
again when the presets or the OpenFHE version change; tests/test_openfhe_context_canonical.py and
the C++ test search_context_preflight fail until you do.

    python server/search/context_golden/generate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "client"))


def main() -> None:
    import openfhe as ofhe
    from ombra.params import load_presets, make_context

    for name in load_presets():
        _, cc = make_context(name)
        (HERE / f"{name}.bin").write_bytes(ofhe.Serialize(cc, ofhe.BINARY))
        print("wrote", name)


if __name__ == "__main__":
    main()
