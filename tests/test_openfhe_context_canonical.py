# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The context files the server embeds equal what the real client writes, for every preset.

The server accepts an uploaded crypto context only if its bytes equal the server's own build of the
preset's context or server/search/context_golden/<preset>.bin (server/search/src/
context_preflight.hpp). This test keeps the golden files honest: it rebuilds the context with the
client code (client/ombra/params.py, openfhe-python) and compares bytes, so a change of preset,
client parameter code or OpenFHE version fails here until `context_golden/generate.py` is run again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("openfhe")

pytestmark = pytest.mark.openfhe

GOLDEN = Path(__file__).resolve().parents[1] / "server" / "search" / "context_golden"


def test_golden_context_files_equal_the_clients_bytes() -> None:
    import openfhe as ofhe
    from ombra.params import load_presets, make_context

    presets = load_presets()
    assert presets
    for name in presets:
        golden = (GOLDEN / f"{name}.bin").read_bytes()
        _, cc = make_context(name)
        assert ofhe.Serialize(cc, ofhe.BINARY) == golden, name
        # Generating keys does not change the context's bytes.
        cc.KeyGen()
        assert ofhe.Serialize(cc, ofhe.BINARY) == golden, name
    assert {p.stem for p in GOLDEN.glob("*.bin")} == set(presets)
