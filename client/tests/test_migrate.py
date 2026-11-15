# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""ombra.migrate with the in-memory FakeClient: the state machine, the gates, resuming, dual write,
the alias switch and the collection info format. No OpenFHE, no server. The same flow against real
encryption is in test_migrate_local.py (marked openfhe)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from ombra.collection_info import INFO_ID, InfoError, pack_info, unpack_info
from ombra.errors import AliasConflictError, OmbraError
from ombra.migrate import (
    Migration,
    MigrationError,
    State,
    load_state,
    main,
    target_name_for,
)
from ombra.testing import FakeClient

N, D = 40, 384


def _vectors(seed: int, dim: int = D, n: int = N) -> np.ndarray:
    x = np.random.default_rng(seed).normal(size=(n, dim))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _client() -> FakeClient:
    c = FakeClient("memory")
    v = _vectors(1)
    c.create_collection(
        "docs",
        v,
        [f"id{i}" for i in range(N)],
        key_file="unused",
        passphrase="x",
        documents=[f"text {i}" for i in range(N)],
    )
    return c


def _embed(texts: list[str]) -> np.ndarray:
    """A deterministic 'model' of another dimension: a vector per text."""
    return np.stack(
        [np.random.default_rng(int(t.split()[-1]) + 100).normal(size=768) for t in texts]
    )


def _plan(c: FakeClient, tmp_path: Path, **kw) -> Migration:
    return Migration.plan(
        c,
        "docs",
        source_key_file=tmp_path / "s.key",
        target_key_file=tmp_path / "t.key",
        source_passphrase="a",
        target_passphrase="b",
        state_path=tmp_path / "state.json",
        **kw,
    )


def test_target_name() -> None:
    assert target_name_for("docs", ["docs"]) == ("docs.m1", 1)
    assert target_name_for("docs", ["docs", "docs.m1", "docs.m3", "other.m9"]) == ("docs.m4", 4)
    assert target_name_for("docs.m2", ["docs", "docs.m2"]) == ("docs.m3", 3)


def test_rebuild_flow_with_alias(tmp_path: Path) -> None:
    c = _client()
    c.create_alias("live", "docs")
    clock = [1000.0]
    m = _plan(c, tmp_path, alias="live", drain_hours=1, now=lambda: clock[0], batch_size=7)
    assert m.state.target == "docs.m1" and m.state.mode == "rebuild"
    m.start(sample=5)
    assert len(list(c.iter_records("docs.m1", ids_only=True))) == 5
    assert m.backfill() == N - 5
    assert m.backfill() == 0  # nothing left: safe to repeat
    res = m.verify(sample=10, k=5)
    assert res["passed"] and res["recall"] == 1.0 and res["overlap_with_source"] == 1.0
    assert m.switch().startswith("alias 'live' now points at 'docs.m1'")
    a = c.list_aliases()[0]
    assert (a.target, a.previous) == ("docs.m1", "docs")
    # the drain period must pass
    with pytest.raises(MigrationError, match="drain"):
        m.finish()
    clock[0] += 3601
    m.finish()
    assert c.list_collections() == ["docs.m1"]
    assert load_state(tmp_path / "state.json").phase == "finished"


def test_state_file_has_no_secrets_and_a_version(tmp_path: Path) -> None:
    c = _client()
    _plan(c, tmp_path)
    raw = (tmp_path / "state.json").read_text()
    d = json.loads(raw)
    assert d["format"] == "ombra-migrate" and d["version"] == 1
    assert '"a"' not in raw and '"b"' not in raw  # the passphrases are never written
    d["version"] = 2
    with pytest.raises(MigrationError, match="version"):
        State.from_json(json.dumps(d))
    with pytest.raises(MigrationError, match="not an ombra-migrate"):
        State.from_json("{}")


def test_plan_refuses_an_existing_state_or_key_file(tmp_path: Path) -> None:
    c = _client()
    _plan(c, tmp_path)
    with pytest.raises(MigrationError, match="exists"):
        _plan(c, tmp_path)
    (tmp_path / "state.json").unlink()
    (tmp_path / "t.key").write_text("x")
    with pytest.raises(MigrationError, match="key file exists"):
        _plan(c, tmp_path)


def test_steps_run_in_order(tmp_path: Path) -> None:
    c = _client()
    m = _plan(c, tmp_path)
    with pytest.raises(MigrationError, match="phase"):
        m.backfill()
    m.start()
    with pytest.raises(MigrationError, match="phase"):
        m.switch()  # not verified
    with pytest.raises(MigrationError, match="phase"):
        m.finish()


def test_resume_after_a_stop_and_dual_write(tmp_path: Path) -> None:
    c = _client()
    m = _plan(c, tmp_path, batch_size=4)
    m.start(sample=3)
    # a new process: resume from the state file
    m2 = Migration.resume(
        c, state_path=tmp_path / "state.json", source_passphrase="a", target_passphrase="b"
    )
    m2.start()  # the target exists: nothing is created twice
    m2.backfill()
    # the application writes during the migration
    new = _vectors(2, n=2)
    m2.insert(new, ["n0", "n1"], documents=["text 90", "text 91"])
    m2.delete(["id0", "n1"])
    m2.verify(sample=5, k=3)
    assert {r.id for r in c.iter_records("docs", ids_only=True)} == {
        r.id for r in c.iter_records("docs.m1", ids_only=True)
    }
    # a write after the pass makes the migration unverified again
    m2.insert(_vectors(3, n=1), ["late"], documents=["text 92"])
    assert m2.state.phase == "backfilled" and m2.state.verify is None


def test_reembed_flow_and_model_label(tmp_path: Path) -> None:
    c = _client()
    with pytest.raises(MigrationError, match="model label"):
        _plan(c, tmp_path, embed=_embed)
    m = _plan(c, tmp_path, embed=_embed, model="model-b")
    assert m.state.mode == "reembed"
    m.start(sample=4)
    m.backfill()
    assert c.get_collection("docs.m1").dimension == 768
    info = c.get_collection_info("docs.m1")
    assert info is not None and info["model"] == "model-b" and info["source"] == "docs"
    res = m.verify(sample=8, k=3)
    assert res["passed"]
    assert res["overlap_with_source"] is not None  # reported, not a gate
    assert 0.0 <= res["overlap_with_source"] <= 1.0


def test_reembed_needs_documents(tmp_path: Path) -> None:
    c = FakeClient("memory")
    c.create_collection("docs", _vectors(1, n=5), key_file="k", passphrase="x")  # no documents
    m = _plan(c, tmp_path, embed=_embed, model="m")
    with pytest.raises(MigrationError, match="text document"):
        m.start()


def test_a_failed_gate_blocks_the_switch(tmp_path: Path) -> None:
    c = _client()
    m = _plan(c, tmp_path)
    m.start(sample=5)
    m.backfill()
    c.insert("docs", _vectors(4, n=1), ["extra"], documents=["text 77"])  # a write that bypassed
    with pytest.raises(MigrationError, match="counts_equal"):
        m.verify()
    assert m.state.verify is not None and m.state.verify["gates"]["counts_equal"] is False
    with pytest.raises(MigrationError, match="phase"):
        m.switch()
    m.backfill()  # catches up
    m.verify()
    m.switch()


def test_switch_is_a_compare_and_swap(tmp_path: Path) -> None:
    c = _client()
    c.create_alias("live", "docs")
    m = _plan(c, tmp_path, alias="live")
    m.start()
    m.backfill()
    m.verify()
    c.create_collection("other", _vectors(5), key_file="k", passphrase="x")
    c.switch_alias("live", "other", "docs")  # someone else moved it first
    with pytest.raises(MigrationError, match="does not point at the source"):
        m.switch()
    assert c.list_aliases()[0].target == "other"


def test_source_given_as_an_alias(tmp_path: Path) -> None:
    c = _client()
    c.create_alias("live", "docs")
    m = Migration.plan(
        c,
        "live",
        source_key_file=tmp_path / "s.key",
        target_key_file=tmp_path / "t.key",
        source_passphrase="a",
        target_passphrase="b",
        state_path=tmp_path / "state.json",
    )
    assert m.state.source == "docs" and m.state.alias == "live"


def test_abort_drops_the_target_only(tmp_path: Path) -> None:
    c = _client()
    m = _plan(c, tmp_path)
    m.start()
    m.backfill()
    m.abort()
    assert c.list_collections() == ["docs"] and m.state.phase == "aborted"


def test_abort_is_refused_after_the_switch(tmp_path: Path) -> None:
    c = _client()
    m = _plan(c, tmp_path)
    m.start()
    m.backfill()
    m.verify()
    m.switch()
    assert "no alias was moved" in m._switch_text()
    with pytest.raises(MigrationError, match="switched"):
        m.abort()


def test_fake_alias_rules() -> None:
    c = _client()
    with pytest.raises(OmbraError):
        c.create_alias("docs", "docs")  # a collection's name
    with pytest.raises(OmbraError):
        c.create_alias("docs.g3", "docs")  # a generation name
    c.create_alias("live", "docs")
    with pytest.raises(OmbraError):
        c.drop_collection("docs")  # an alias points at it
    with pytest.raises(AliasConflictError):
        c.switch_alias("live", "docs", "nope")
    assert c.open_collection("live", key_file="k", passphrase="x") == "docs"


def test_collection_info_format() -> None:
    blob = pack_info({"model": "m1", "migration": 2})
    assert blob[:4] == b"OINF" and len(blob) % 4 == 0
    assert unpack_info(blob) == {"model": "m1", "migration": 2}
    with pytest.raises(InfoError, match="version"):
        unpack_info(b"OINF\x02\x00" + blob[6:])
    with pytest.raises(InfoError):
        unpack_info(b"XXXX" + blob[4:])
    with pytest.raises(ValueError, match="4096"):
        pack_info({"x": "y" * 5000})
    assert INFO_ID.startswith(b"\x00ombra/")


def test_cli_status_and_missing_state(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status", "--state", str(tmp_path / "none.json")]) == 1
    c = _client()
    _plan(c, tmp_path)
    assert main(["status", "--state", str(tmp_path / "state.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["phase"] == "planned" and out["target"] == "docs.m1"
