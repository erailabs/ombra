# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""GPU inner products of the private record retrieval answer, against OpenFHE.

The C++ driver `ombra_pir_inner_check` builds the retrieval BFV context of the preset
`pir_bfv_n8192`, synthetic query ciphertexts and random synthetic cells, then computes
inner_b = sum_a EvalMult(sel1_a, cell[a * r2 + b]) with OpenFHE and on the GPU. It exits 0 only if
  * every RNS word of every inner_b is equal, exactly,
  * the GPU result put back into an OpenFHE ciphertext is identical to OpenFHE's (metadata, words),
  * the rest of the answer on OpenFHE (EvalMultNoRelin, Relinearize, Compress) gives byte-identical
    serialized answers from both, decrypting to the same plaintext and to the one computed mod t.
Keys and decryption stay inside the driver; nothing but PASS/FAIL lines and counts is printed.
The two injection modes break the GPU side on purpose and the driver must then fail: they show that
a wrong word and a wrong plaintext lift (centered values instead of the [0, t) ones OpenFHE lifts,
which decrypt the same but are not the same ciphertext) are noticed.
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
        os.environ.get("OMBRA_PIR_INNER_CHECK_BIN", ""),
        "/root/ombra-build/core/ombra_pir_inner_check",
        str(REPO_ROOT / "build" / "core" / "ombra_pir_inner_check"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    pytest.fail("ombra_pir_inner_check not found; build with `make build` or run `make test-gpu`")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # noqa: S603
        [str(_driver()), *args], capture_output=True, text=True, timeout=900
    )
    print(proc.stdout)
    print(proc.stderr)
    return proc


@pytest.mark.parametrize(("r1", "r2", "seed"), [(3, 2, 1), (1, 1, 2), (5, 4, 3), (2, 7, 4)])
def test_gpu_pir_inner_matches_openfhe(r1: int, r2: int, seed: int) -> None:
    proc = _run(str(r1), str(r2), str(seed))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for line in proc.stdout.splitlines():
        if ":" in line and not line.startswith("RESULT"):
            assert line.endswith("PASS"), line
    assert "GPU inner words equal OpenFHE EvalMult+EvalAdd, exact" in proc.stdout
    assert "serialized answers byte-identical" in proc.stdout
    assert "RESULT PASS" in proc.stdout


@pytest.mark.parametrize("inject", ["word", "centered"])
def test_gpu_pir_inner_oracle_detects_faults(inject: str) -> None:
    proc = _run("3", "2", "1", inject)
    assert proc.returncode == 1, f"driver did not fail (exit {proc.returncode}): {proc.stdout}"
    assert any(x.endswith("FAIL") for x in proc.stdout.splitlines())
    assert "RESULT FAIL" in proc.stdout
