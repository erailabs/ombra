# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The scheme field of the preset file and the BFV preset for exact retrieval."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from ombra import params, presets
from ombra.params import ParamsError

ROOT = Path(__file__).resolve().parents[2]
JSON = ROOT / "core" / "params" / "ckks_presets.json"
BFV = "pir_bfv_n8192"
# Largest log2(QP) the HE standard allows for 128-bit classic security (same table as
# test_ckks_baseline.py).
HE_STD_128_CLASSIC_MAX_LOG_QP = {1024: 27, 2048: 54, 4096: 109, 8192: 218, 16384: 438, 32768: 881}


def _file(tmp_path: Path, mutate: Any = None, version: int = 2) -> Path:
    data = json.loads(JSON.read_text())
    data["version"] = version
    if mutate:
        mutate(data["presets"])
    p = tmp_path / "p.json"
    p.write_text(json.dumps(data))
    return p


@pytest.mark.parametrize("version,ok", [(1, False), (2, True), (3, False)])
def test_file_version(tmp_path: Path, version: int, ok: bool) -> None:
    p = _file(tmp_path, version=version)
    if ok:
        assert params.load_presets(p)
    else:
        with pytest.raises(ParamsError, match="version"):
            params.load_presets(p)


def test_missing_scheme_is_ckks() -> None:
    assert "scheme" not in params.get_preset("m0_depth2")
    assert params.preset_scheme(params.get_preset("m0_depth2")) == "CKKS"


def test_unknown_scheme_refused(tmp_path: Path) -> None:
    p = _file(tmp_path, lambda d: d["m0_depth2"].update(scheme="TFHE"))
    with pytest.raises(ParamsError, match="unknown scheme"):
        params.load_presets(p)
    with pytest.raises(ParamsError, match="unknown scheme"):
        params.validate_preset({"scheme": "TFHE", "security_level": "HEStd_128_classic"})


def test_default_listing_has_only_ckks() -> None:
    assert sorted(params.load_presets()) == ["m0_compact_n8192", "m0_depth2"]
    assert BFV in params.load_presets(scheme="BFV")
    assert BFV in params.load_presets(scheme=None)


def test_bfv_preset_is_not_a_collection_preset() -> None:
    with pytest.raises(ParamsError, match="unknown"):
        presets.derive(BFV, 384)
    with pytest.raises(ParamsError):
        params.get_preset(BFV)
    with pytest.raises(ParamsError):
        params.make_context(BFV)


def test_bfv_missing_plaintext_modulus_refused() -> None:
    p = dict(params.get_preset(BFV, scheme="BFV"))
    del p["plaintext_modulus"]
    with pytest.raises(ParamsError, match="missing keys"):
        params.validate_preset(p)


def test_bfv_below_floor_refused() -> None:
    p = dict(params.get_preset(BFV, scheme="BFV"))
    p["security_level"] = "HEStd_NotSet"
    with pytest.raises(ParamsError, match="below 128 bits"):
        params.make_bfv_params(p)


def test_scheme_mismatch_refused() -> None:
    with pytest.raises(ParamsError, match="CKKS preset"):
        params.make_ckks_params(params.get_preset(BFV, scheme="BFV"))
    with pytest.raises(ParamsError, match="BFV preset"):
        params.make_bfv_params(params.get_preset("m0_depth2"))


def test_bfv_preset_values() -> None:
    p = params.get_preset(BFV, scheme="BFV")
    assert p["security_level"] in params.ACCEPTED_SECURITY_LEVELS
    assert (p["ring_dim"], p["plaintext_modulus"]) == (8192, 786433)


def _evalkey_moduli(tree: Any) -> list[int]:
    """RNS moduli of the towers of the first polynomial of a serialized EvalMult key (JSON),
    read the way test_ckks_baseline.py does (cereal layout of OpenFHE v1.6.1)."""
    key_entry = tree["value0"][0]["value"][0]
    dcrt = key_entry["ptr_wrapper"]["data"]["ak"][0]
    return [int(t["v"]["ptr_wrapper"]["data"]["m"]["v"]) for t in dcrt["v"]]


@pytest.mark.openfhe
def test_bfv_context_security_and_round_trip(tmp_path: Path) -> None:
    ofhe = pytest.importorskip("openfhe")
    prm, cc = params.make_bfv_context(BFV)
    assert prm.GetSecurityLevel() == ofhe.SecurityLevel.HEStd_128_classic
    ring = cc.GetRingDimension()
    assert ring == 8192
    keys = cc.KeyGen()
    cc.EvalMultKeyGen(keys.secretKey)
    q_bits = math.log2(cc.GetElementParams().GetModulus())
    key_file = tmp_path / "evalmult.json"
    assert ofhe.CryptoContext.SerializeEvalMultKey(
        str(key_file), ofhe.JSON, keys.publicKey.GetKeyTag()
    )
    mods = _evalkey_moduli(json.loads(key_file.read_text()))
    log_qp = sum(math.log2(m) for m in mods)
    print(f"BFV log2(Q)={q_bits:.2f} key moduli={len(mods)} log2(QP)={log_qp:.2f}")
    # BV key switching has no special modulus P, so the key lives in Q alone.
    assert len(mods) == prm.GetMultiplicativeDepth() + 1
    assert abs(log_qp - q_bits) < 1e-6
    assert log_qp <= HE_STD_128_CLASSIC_MAX_LOG_QP[ring]

    t = params.get_preset(BFV, scheme="BFV")["plaintext_modulus"]
    half = (t - 1) // 2
    values = [(i * 7919) % (2 * half + 1) - half for i in range(ring)]
    values[0], values[1] = -half, half
    ct = cc.Encrypt(keys.publicKey, cc.MakeCoefPackedPlaintext(values))
    pt = cc.Decrypt(ct, keys.secretKey)
    pt.SetLength(ring)
    assert list(pt.GetCoefPackedValue()) == values
