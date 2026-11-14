# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Key files of the real client against the structure templates the server embeds.

Before OpenFHE (cereal) reads an EvalMult or automorphism key file, the server checks that every
structure byte (lengths, counts, versions, moduli, tag length, map size, rotation indices) equals
the template of the preset; only the coefficient words may differ (server/search/src/
context_preflight.hpp). This file checks, with synthetic keys from fresh client key generations
that were not used to build the templates:

- the templates equal what context_golden/generate.py builds now (so a change of preset, client or
  OpenFHE fails here until the generator is run again);
- every key file the client writes for a collection is accepted, for several key counts;
- a change of any structure byte is refused and a change of a coefficient byte is accepted
  (the C++ tool search_key_file_check, `make build-cpu`).

Balanced keys are large (up to 37 MB per file), so each preset's files are made once per session.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("openfhe")

pytestmark = pytest.mark.openfhe

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "server" / "search" / "context_golden"
FRESH_KEY_SETS = 3


def _tool() -> Path:
    env = os.environ.get("OMBRA_KEY_CHECK_BIN")
    path = Path(env) if env else ROOT / "build-cpu" / "server" / "search" / "search_key_file_check"
    if not path.is_file():
        pytest.skip("search_key_file_check not built: run `make build-cpu`")
    return path


def _generator():
    spec = importlib.util.spec_from_file_location("context_golden_generate", GOLDEN / "generate.py")
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    return gen


def _presets() -> list[str]:
    from ombra.params import load_presets

    return list(load_presets())


def test_templates_equal_what_the_generator_builds_now() -> None:
    gen = _generator()
    for name in _presets():
        mult, auto, _ = gen.build_templates(name)
        assert (GOLDEN / f"{name}.evalmult_keys").read_bytes() == mult, name
        assert (GOLDEN / f"{name}.auto_keys").read_bytes() == auto, name


@pytest.fixture(scope="session")
def key_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[dict[str, Path]]]:
    """For each preset, FRESH_KEY_SETS independent client key generations written to files:
    the EvalMult file and automorphism files for 1, 2, 5 and the maximum number of keys."""
    import openfhe as ofhe
    from ombra.params import make_context

    out: dict[str, list[dict[str, Path]]] = {}
    for name in _presets():
        _, cc0 = make_context(name)
        top = cc0.GetRingDimension().bit_length() - 1
        sets = []
        for i in range(FRESH_KEY_SETS):
            d = tmp_path_factory.mktemp(f"{name}_{i}")
            files: dict[str, Path] = {}
            for n_keys in (1, 2, 5, top):
                _, cc = make_context(name)
                ring = cc.GetRingDimension()
                kp = cc.KeyGen()
                tag = kp.publicKey.GetKeyTag()
                if n_keys == 1:
                    cc.EvalMultKeyGen(kp.secretKey)
                    path = d / "evalmult"
                    path.write_bytes(ofhe.SerializeEvalMultKeyString(ofhe.BINARY, tag))
                    files["evalmult"] = path
                cc.EvalAutomorphismKeyGen(kp.secretKey, [ring // (1 << k) + 1 for k in range(n_keys)])
                path = d / f"auto{n_keys}"
                path.write_bytes(ofhe.SerializeEvalAutomorphismKeyString(ofhe.BINARY, tag))
                files[f"auto{n_keys}"] = path
            sets.append(files)
        out[name] = sets
    return out


def _run(tool: Path, preset: str, kind: str, path: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(tool), preset, kind, str(path), *extra], capture_output=True, text=True, timeout=600
    )


def test_fresh_client_key_files_are_accepted(key_files) -> None:
    tool = _tool()
    for preset, sets in key_files.items():
        assert len(sets) == FRESH_KEY_SETS
        for files in sets:
            for label, path in files.items():
                kind = "evalmult" if label == "evalmult" else "auto"
                r = _run(tool, preset, kind, path)
                assert r.returncode == 0, (preset, label, r.stdout, r.stderr)


def test_every_structure_byte_matters(key_files) -> None:
    """A flipped structure byte is refused; a changed coefficient byte is accepted."""
    tool = _tool()
    for preset, sets in key_files.items():
        files = sets[0]
        for label, path in files.items():
            kind = "evalmult" if label == "evalmult" else "auto"
            step = "1" if label in ("evalmult", "auto1", "auto2") else "37"
            r = _run(tool, preset, kind, path, "flips", step)
            assert r.returncode == 0, (preset, label, r.stdout, r.stderr)
            assert "wrongly_accepted 0" in r.stdout, r.stdout


def test_other_files_are_refused(key_files, tmp_path: Path) -> None:
    tool = _tool()
    for preset, sets in key_files.items():
        files = sets[0]
        # The two kinds are not interchangeable, and a file of the other preset is refused.
        assert _run(tool, preset, "auto", files["evalmult"]).returncode == 1
        assert _run(tool, preset, "evalmult", files["auto2"]).returncode == 1
        other = next(p for p in key_files if p != preset)
        assert _run(tool, other, "evalmult", files["evalmult"]).returncode == 1
        # Truncated and extended files.
        raw = files["evalmult"].read_bytes()
        for name, data in (("short", raw[:-1]), ("long", raw + b"\0"), ("empty", b"")):
            p = tmp_path / f"{preset}_{name}"
            p.write_bytes(data)
            assert _run(tool, preset, "evalmult", p).returncode == 1, name
