# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""ombra-migrate against real encryption in local mode (CPU OpenFHE, small sizes): rebuild and
re-embed, documents and filter fields carried over, the collection info, resuming, and the command
line. Local mode has no aliases, so `switch` records the move and says so. Synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("openfhe")
pytest.importorskip("grpc_tools")
pytest.importorskip("google.protobuf")

from ombra import Client  # noqa: E402
from ombra.errors import InvalidArgumentError  # noqa: E402
from ombra.migrate import Migration, MigrationError, main  # noqa: E402

pytestmark = pytest.mark.openfhe

D, N = 384, 10
SCRYPT = (2**4, 8, 1)


def _unit(seed: int, n: int = N) -> np.ndarray:
    x = np.random.default_rng(seed).normal(size=(n, D))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _make_source(root: Path) -> tuple[Client, np.ndarray]:
    c = Client.local(root / "data")
    x = _unit(1)
    c.create_collection(
        "docs",
        x,
        [f"id{i}" for i in range(N)],
        key_file=root / "docs.key",
        passphrase="old",
        metadata=[{"tag": i % 3, "n": i} for i in range(N)],
        documents=[f"doc {i}" for i in range(N)],
        filter_fields={"tag": 3},
        n_clusters=1,
        scrypt_params=SCRYPT,
    )
    return c, x


def _embed(texts: list[str]) -> np.ndarray:
    """A 'new model': other vectors for the same texts, same dimension."""
    return np.stack([np.random.default_rng(int(t.split()[-1]) + 500).normal(size=D) for t in texts])


def test_local_mode_has_no_aliases(tmp_path: Path) -> None:
    c = Client.local(tmp_path / "data")
    try:
        with pytest.raises(InvalidArgumentError, match="local mode"):
            c.create_alias("live", "docs")
        with pytest.raises(InvalidArgumentError):
            c.list_aliases()
    finally:
        c.close()


def test_rebuild_keeps_everything(tmp_path: Path) -> None:
    c, x = _make_source(tmp_path)
    try:
        m = Migration.plan(
            c,
            "docs",
            source_key_file=tmp_path / "docs.key",
            target_key_file=tmp_path / "docs.m1.key",
            source_passphrase="old",
            target_passphrase="new",
            state_path=tmp_path / "state.json",
            filter_fields=["tag"],
            scrypt_params=SCRYPT,
            batch_size=4,
        )
        m.start(sample=3)
        assert m.backfill() == N - 3
        res = m.verify(sample=4, k=3, min_recall=0.5)
        assert res["passed"] and res["source_live"] == res["target_live"] == N
        assert "no alias was moved" in m.switch()
        # the new collection answers like the old one, with its own key file
        hits = c.search("docs.m1", x[2], k=1, filter={"tag": 2})
        assert [h.id for h in hits] == ["id2"]
        assert hits[0].metadata["n"] == 2
        recs = {r.id: r for r in c.iter_records("docs.m1")}
        assert recs["id4"].document == "doc 4"
        assert np.allclose(recs["id4"].vector, x[4].astype("<f4"))
        assert c.get_collection_info("docs.m1")["migration"] == 1
        assert (tmp_path / "docs.m1.key").exists()
        m.finish(force=True)
        assert c.list_collections() == ["docs.m1"]
        raw = (tmp_path / "state.json").read_text()
        assert "old" not in json.loads(raw)["target_key_file"] and '"new"' not in raw
    finally:
        c.close()


def test_reembed_and_resume(tmp_path: Path) -> None:
    c, x = _make_source(tmp_path)
    try:
        m = Migration.plan(
            c,
            "docs",
            source_key_file=tmp_path / "docs.key",
            target_key_file=tmp_path / "docs.m1.key",
            source_passphrase="old",
            target_passphrase="new",
            state_path=tmp_path / "state.json",
            filter_fields=["tag"],
            embed=_embed,
            model="model-b",
            scrypt_params=SCRYPT,
        )
        m.start(sample=4)
        # a new process: a new Migration object from the state file
        m2 = Migration.resume(
            c,
            state_path=tmp_path / "state.json",
            source_passphrase="old",
            target_passphrase="new",
            embed=_embed,
            scrypt_params=SCRYPT,
        )
        m2.backfill()
        info = c.get_collection_info("docs.m1")
        assert info is not None and info["model"] == "model-b"
        # a dual write during the migration
        m2.insert(_unit(9, 1), ["late"], [{"tag": 1, "n": 99}], ["doc 77"])
        m2.backfill()
        res = m2.verify(sample=4, k=3, min_recall=0.5)
        assert res["passed"]
        q = _embed(["doc 5"])[0]
        q = q / np.linalg.norm(q)
        assert c.search("docs.m1", q, k=1)[0].id == "id5"
        # the old model's vector finds nothing useful in the new collection: the labels differ
        with pytest.raises(MigrationError, match="phase"):
            m2.finish()
    finally:
        c.close()


def test_cli_full_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    c, _ = _make_source(tmp_path)
    c.close()
    monkeypatch.setenv("OMBRA_PASSPHRASE", "old")
    monkeypatch.setenv("OMBRA_TARGET_PASSPHRASE", "new")
    st = str(tmp_path / "state.json")
    base = ["--state", st, "--local", str(tmp_path / "data")]
    assert (
        main(
            [
                "plan",
                *base,
                "--source",
                "docs",
                "--source-key-file",
                str(tmp_path / "docs.key"),
                "--target-key-file",
                str(tmp_path / "docs.m1.key"),
                "--filter-fields",
                "tag",
                "--model",
                "same",
            ]
        )
        == 0
    )
    assert main(["start", *base]) == 0
    assert main(["backfill", *base]) == 0
    assert main(["verify", *base, "--sample", "3", "-k", "3", "--min-recall", "0.5"]) == 0
    assert main(["switch", *base]) == 0
    assert main(["finish", *base]) == 1  # the drain period is not over
    assert "drain" in capsys.readouterr().err
    assert main(["finish", *base, "--force"]) == 0
    assert main(["status", "--state", st]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["phase"] == "finished"


# ---- key rotation (decision 0031, option B) ------------------------------------------------
def _rotate(c: Client, root: Path, **kw) -> Migration:
    return Migration.plan(
        c,
        "docs",
        source_key_file=root / "docs.key",
        target_key_file=root / "docs.m1.key",
        source_passphrase="old",
        target_passphrase="new",
        state_path=root / "state.json",
        filter_fields=["tag"],
        scrypt_params=SCRYPT,
        batch_size=4,
        new_keys=True,
        **kw,
    )


def _blob(c: Client, coll: str, blob_id: bytes) -> bytes:
    from ombra.blobstore import KIND_RECORD

    col = c._core._open[coll][0]
    return col.blobs.get(coll, KIND_RECORD, blob_id)


def test_rotation_round_trip_and_keys_are_new(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    from ombra.documents import DocumentCodec, DocumentError
    from ombra.keyfile import import_key_file

    c, x = _make_source(tmp_path)
    try:
        before = {i: [h.id for h in c.search("docs", x[i], k=5)] for i in (0, 3, 7)}
        m = _rotate(c, tmp_path)
        m.start(sample=3)
        m.backfill()
        m.verify(sample=4, k=3, min_recall=0.5)
        m.switch()
        after = {i: [h.id for h in c.search("docs.m1", x[i], k=5)] for i in (0, 3, 7)}
        assert after == before  # the same answers on the same data
        recs = {r.id: r for r in c.iter_records("docs.m1")}
        assert recs["id4"].document == "doc 4" and recs["id4"].metadata["n"] == 4

        old = import_key_file(tmp_path / "docs.key", "old")
        new = import_key_file(tmp_path / "docs.m1.key", "new")
        assert old.secret_key != new.secret_key  # a new FHE key pair
        assert old.master_key != new.master_key  # and a new master key

        # the old key file is refused on the new collection (key tag mismatch)
        with pytest.raises(ValueError, match="does not belong"):
            c.open_collection("docs.m1", key_file=tmp_path / "docs.key", passphrase="old")

        # blobs: sealed under one master key, closed to the other
        bid = b"id3"
        old_codec = DocumentCodec(old.master_key, "docs.m1")
        new_codec = DocumentCodec(new.master_key, "docs.m1")
        blob_new = _blob(c, "docs.m1", bid)
        assert new_codec.open_record(bid, blob_new).vector
        with pytest.raises(DocumentError):
            old_codec.open_record(bid, blob_new)
        from ombra.probe import STATS_ID

        for special in (STATS_ID,):  # the stats blob is re-sealed under the new master key too
            blob = _blob(c, "docs.m1", special)
            with pytest.raises(DocumentError):
                old_codec.open_record(special, blob)
            new_codec.open_record(special, blob)
        sealed_old = old_codec.seal_record(bid, b"\0\0\0\0", {})
        with pytest.raises(DocumentError):
            new_codec.open_record(bid, sealed_old)

        # no secret material in the state file or in anything printed or logged
        state = (tmp_path / "state.json").read_bytes()
        out = capsys.readouterr()
        text = state + out.out.encode() + out.err.encode() + caplog.text.encode()
        for secret in (old.secret_key, new.secret_key, old.master_key, new.master_key):
            assert secret not in text and secret.hex().encode() not in text
    finally:
        c.close()


def test_rotation_crash_between_segments_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, x = _make_source(tmp_path)
    try:
        m = _rotate(c, tmp_path)
        m.start(sample=2)
        real, calls = c.insert, [0]

        def dying(*a, **kw):
            calls[0] += 1
            if calls[0] == 2:
                raise KeyboardInterrupt
            return real(*a, **kw)

        monkeypatch.setattr(c, "insert", dying)
        with pytest.raises(KeyboardInterrupt):
            m.backfill()
        monkeypatch.setattr(c, "insert", real)
        m2 = Migration.resume(
            c,
            state_path=tmp_path / "state.json",
            source_passphrase="old",
            target_passphrase="new",
            scrypt_params=SCRYPT,
            batch_size=4,
        )
        m2.backfill()
        res = m2.verify(sample=4, k=3, min_recall=0.5)
        assert res["target_live"] == N
        assert c.search("docs.m1", x[6], k=1)[0].id == "id6"
    finally:
        c.close()
