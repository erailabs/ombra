# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The fixtures of the C++ retrieval layout (server/pir/tests/golden) are what the Python code
produces now. Regenerate with `python server/pir/tests/generate_golden.py` if this fails after an
intended change of client/ombra/pir.py (and then the C++ port must follow)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("cryptography")

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "server" / "pir" / "tests" / "generate_golden.py"
GOLDEN = ROOT / "server" / "pir" / "tests" / "golden"


def _generator():
    spec = importlib.util.spec_from_file_location("pir_generate_golden", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_layout_fixture_is_current() -> None:
    assert (GOLDEN / "layout.txt").read_text() == _generator().layout_text()


def test_blake2b_fixture_is_current() -> None:
    assert (GOLDEN / "blake2b.txt").read_text() == _generator().blake2b_text()


def test_documents_fixture_is_current() -> None:
    assert (GOLDEN / "documents.txt").read_text() == _generator().documents_text()
