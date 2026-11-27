# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""GPU second-dimension product of the private record retrieval answer, against OpenFHE.

The C++ driver `ombra_pir_product_check` builds the retrieval BFV context of the preset
`pir_bfv_n8192` and r2 pairs of synthetic ciphertexts (a selector and an inner sum), then computes
total = sum_b EvalMultNoRelin(x_b, y_b) with OpenFHE and on the GPU. It exits 0 only if
  * every RNS word of the three components of the total is equal, exactly,
  * the GPU total put back into an OpenFHE ciphertext is identical to OpenFHE's (metadata, words),
  * Relinearize and Compress on OpenFHE then give byte-identical serialized answers.
Keys stay inside the driver; nothing but PASS/FAIL lines and counts is printed. The injection mode
breaks one GPU word on purpose and the driver must then fail.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.gpu

REPO_ROOT = Path(__file__).resolve().parents[1]


def _driver() -> Path:
    candidates = [
        os.environ.get("OMBRA_PIR_PRODUCT_CHECK_BIN", ""),
        "/root/ombra-build/core/ombra_pir_product_check",
        str(REPO_ROOT / "build" / "core" / "ombra_pir_product_check"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    pytest.fail("ombra_pir_product_check not found; build with `make build` or run `make test-gpu`")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # noqa: S603
        [str(_driver()), *args], capture_output=True, text=True, timeout=900
    )
    print(proc.stdout)
    print(proc.stderr)
    return proc


# One pair, an odd count, a count that is no multiple of the NTT or grid batch, a larger one.
@pytest.mark.parametrize(("r2", "seed"), [(1, 1), (2, 2), (3, 3), (7, 4), (33, 5)])
def test_gpu_pir_product_matches_openfhe(r2: int, seed: int) -> None:
    proc = _run(str(r2), str(seed))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for line in proc.stdout.splitlines():
        if ":" in line and not line.startswith("RESULT"):
            assert line.endswith("PASS"), line
    assert "exact" in proc.stdout
    assert "serialized answers byte-identical" in proc.stdout
    assert "RESULT PASS" in proc.stdout


def test_gpu_pir_product_oracle_detects_a_wrong_word() -> None:
    proc = _run("3", "1", "word")
    assert proc.returncode == 1, f"driver did not fail (exit {proc.returncode}): {proc.stdout}"
    assert any(x.endswith("FAIL") for x in proc.stdout.splitlines())
    assert "RESULT FAIL" in proc.stdout
