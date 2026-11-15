# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Collection aliases on the CPU-only `ombra_server` (decision 0028): the alias RPCs and their
errors, resolving only in GetCollection, the catalog upgrade from schema 1 and 2, keys limited to
an alias, and old data directories (catalog schema 1, api_keys v1) that still open.

Collections here are empty (a spec on the server, no keys, no vectors), so nothing is encrypted and
the tests need no OpenFHE. Synthetic names only.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

grpc = pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")
np = pytest.importorskip("numpy")

from server_harness import ServerProcess, find_server_binary  # noqa: E402

if find_server_binary() is None:
    pytest.skip("ombra_server (CPU build) not found: run `make build-cpu`", allow_module_level=True)

from ombra._proto import load_stubs  # noqa: E402

pb, pbg = load_stubs()
OFHE = "1.6.1"  # only compared with the files the server reads; no OpenFHE runs here


def spec(name: str, tag: str = "tag") -> object:
    s = pb.CollectionSpec(
        api_version=1,
        name=name,
        preset="m0_compact_n8192",
        dimension=384,
        security_level=pb.SECURITY_LEVEL_FAST,
        key_tag=tag,
        cluster_count=1,
    )
    return s


class Srv:
    def __init__(self, root, auth=False):
        self.server = ServerProcess(root, auth=auth, openfhe_version=OFHE)
        self.auth = auth
        self.channel = None
        self.stub = None
        self.token = None

    def start(self, token=None):
        self.server.start()
        self.channel = grpc.insecure_channel(self.server.address)
        self.stub = pbg.OmbraStub(self.channel)
        self.token = token

    def stop(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        self.server.kill()

    @property
    def md(self):
        return (("authorization", f"Bearer {self.token}"),) if self.token else ()

    def call(self, rpc, req):
        return getattr(self.stub, rpc)(req, metadata=self.md, timeout=20)

    def create(self, name):
        r = self.call("CreateCollection", pb.CreateCollectionRequest(spec=spec(name)))
        assert r.WhichOneof("result") == "spec", r
        return r

    def alias(self, name, target):
        return self.call("CreateAlias", pb.CreateAliasRequest(name=name, target=target))

    def switch(self, name, new, expected):
        return self.call(
            "SwitchAlias",
            pb.SwitchAliasRequest(name=name, new_target=new, expected_target=expected),
        )

    def get(self, name):
        return self.call("GetCollection", pb.GetCollectionRequest(name=name))

    def drop(self, name):
        return self.call("DropCollection", pb.DropCollectionRequest(name=name))

    def listed(self):
        return {
            a.name: (a.target, a.previous)
            for a in self.call("ListAliases", pb.ListAliasesRequest()).aliases
        }


@pytest.fixture
def srv(tmp_path):
    s = Srv(tmp_path)
    s.start()
    yield s
    s.stop()


def code(resp) -> int:
    if not resp.DESCRIPTOR.oneofs_by_name.get("result"):
        return resp.error.code  # DropCollection, DropAlias: the error is unset on success
    return resp.error.code if resp.WhichOneof("result") == "error" else 0


def test_alias_lifecycle_and_errors(srv):
    srv.create("docs")
    srv.create("docs.m1")
    r = srv.alias("live", "docs")
    assert r.alias.target == "docs" and r.alias.previous == "" and r.alias.switched_at > 0
    assert code(srv.alias("live", "docs")) == pb.ERROR_ALIAS_EXISTS
    assert code(srv.alias("docs", "docs.m1")) == pb.ERROR_ALIAS_EXISTS  # a collection's name
    assert code(srv.alias("docs.g7", "docs")) == pb.ERROR_INVALID_ARGUMENT  # generation name
    assert code(srv.alias("", "docs")) == pb.ERROR_INVALID_ARGUMENT
    assert code(srv.alias("other", "missing")) == pb.ERROR_COLLECTION_NOT_FOUND
    # a collection cannot take an alias's name
    assert code(srv.call("CreateCollection", pb.CreateCollectionRequest(spec=spec("live")))) == (
        pb.ERROR_COLLECTION_EXISTS
    )
    # only GetCollection resolves; data calls name real collections
    got = srv.get("live")
    assert got.info.spec.name == "docs"
    assert srv.get("docs").info.spec.name == "docs"
    r = srv.call("ListBlocks", pb.ListBlocksRequest(api_version=1, collection="live"))
    assert r.error.code == pb.ERROR_COLLECTION_NOT_FOUND
    assert code(srv.drop("live")) == pb.ERROR_COLLECTION_NOT_FOUND  # drop never resolves either
    # compare-and-swap
    assert code(srv.switch("live", "docs.m1", "docs.m1")) == pb.ERROR_ALIAS_CONFLICT
    assert code(srv.switch("none", "docs", "docs")) == pb.ERROR_ALIAS_NOT_FOUND
    assert code(srv.switch("live", "missing", "docs")) == pb.ERROR_COLLECTION_NOT_FOUND
    r = srv.switch("live", "docs.m1", "docs")
    assert (r.alias.target, r.alias.previous) == ("docs.m1", "docs")
    assert srv.get("live").info.spec.name == "docs.m1"
    # a repeat of the same switch (lost answer) succeeds and changes nothing
    r = srv.switch("live", "docs.m1", "docs.m1")
    assert (r.alias.target, r.alias.previous) == ("docs.m1", "docs")
    assert code(srv.switch("live", "docs.m1", "docs")) == pb.ERROR_ALIAS_CONFLICT
    # the target cannot be dropped; the previous one can
    assert code_of_drop(srv, "docs.m1") == pb.ERROR_ALIAS_CONFLICT
    assert code_of_drop(srv, "docs") == 0
    assert srv.listed() == {"live": ("docs.m1", "")}  # the alias forgot the dropped previous
    assert srv.call("DropAlias", pb.DropAliasRequest(name="live")).error.code == 0
    assert (
        srv.call("DropAlias", pb.DropAliasRequest(name="live")).error.code
        == pb.ERROR_ALIAS_NOT_FOUND
    )
    assert code_of_drop(srv, "docs.m1") == 0


def code_of_drop(srv, name) -> int:
    return srv.drop(name).error.code


def test_aliases_survive_a_restart(srv):
    srv.create("a")
    srv.create("b")
    srv.alias("live", "a")
    srv.switch("live", "b", "a")
    srv.server.kill()
    srv.channel.close()
    srv.start()
    assert srv.listed() == {"live": ("b", "a")}


def _downgrade(data_dir, version):
    db = sqlite3.connect(str(data_dir / "catalog.db"))
    db.execute("DROP TABLE aliases")
    if version == 1:
        db.execute("DROP TABLE blobs")
    db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
    db.commit()
    db.close()


@pytest.mark.parametrize("version", [2, 1])
def test_catalog_schema_1_and_2_upgrade_in_place(tmp_path, version):
    """An older catalog (and, for schema 1, no blob table) opens, keeps its collections and gets
    the alias table. The key file is v1 and works as before."""
    s = Srv(tmp_path, auth=True)
    token = s.server.create_key("admin")  # api_keys v1
    s.start(token)
    s.create("docs")
    s.stop()
    _downgrade(tmp_path / "data", version)
    assert (tmp_path / "data" / "api_keys.v1").exists()
    s.start(token)
    try:
        assert s.get("docs").info.spec.name == "docs"
        assert code(s.alias("live", "docs")) == 0
        assert s.listed() == {"live": ("docs", "")}
    finally:
        s.stop()
    db = sqlite3.connect(str(tmp_path / "data" / "catalog.db"))
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == 3
    db.close()


def test_new_catalog_is_schema_3(srv):
    srv.stop()
    db = sqlite3.connect(str(srv.server.data_dir / "catalog.db"))
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == 3
    cols = [r[1] for r in db.execute("PRAGMA table_info(aliases)")]
    assert cols == ["name", "target", "previous", "switched_at"]
    db.close()


def test_a_newer_catalog_is_refused(tmp_path):
    s = Srv(tmp_path)
    s.start()
    s.stop()
    db = sqlite3.connect(str(tmp_path / "data" / "catalog.db"))
    db.execute("UPDATE meta SET value=4 WHERE key='schema_version'")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError):
        s.start()


def test_alias_rpcs_need_admin_and_scoped_keys_follow_the_alias(tmp_path):
    s = Srv(tmp_path, auth=True)
    admin = s.server.create_key("admin")
    scoped = s.server.create_key("write", "live")  # limited to the alias name, v2 key file
    reader = s.server.create_key("read")
    s.start(admin)
    try:
        for n in ("a", "b", "c"):
            s.create(n)
        assert code(s.alias("live", "a")) == 0

        def as_(token):
            s.token = token

        def allowed(name) -> bool:
            as_(scoped)
            r = s.get(name) if name != "live" else s.get("live")
            return r.WhichOneof("result") == "info"

        def denied(name) -> bool:
            as_(scoped)
            try:
                s.call("ListBlocks", pb.ListBlocksRequest(api_version=1, collection=name))
            except grpc.RpcError as e:
                return e.code() == grpc.StatusCode.PERMISSION_DENIED
            return False

        # alias -> a: the key reaches a; b, c and a name that does not exist are denied alike
        assert allowed("a") and allowed("live")
        assert denied("b") and denied("c") and denied("nothing")
        # switch to b: a is the previous target and still allowed (the drain window)
        as_(admin)
        assert code(s.switch("live", "b", "a")) == 0
        assert allowed("a") and allowed("b")
        assert denied("c") and denied("nothing")
        # switch to c: a is no longer previous
        as_(admin)
        assert code(s.switch("live", "c", "b")) == 0
        assert denied("a") and allowed("b") and allowed("c")
        # the scoped key lists its alias's collections and only its alias
        as_(scoped)
        assert [
            c.spec.name for c in s.call("ListCollections", pb.ListCollectionsRequest()).collections
        ] == [
            "b",
            "c",
        ]
        assert set(s.listed()) == {"live"}
        # changing aliases needs admin: write and read keys are refused
        for token in (scoped, reader):
            as_(token)
            with pytest.raises(grpc.RpcError) as ei:
                s.alias("x", "a")
            assert ei.value.code() == grpc.StatusCode.PERMISSION_DENIED
            with pytest.raises(grpc.RpcError) as ei:
                s.switch("live", "a", "c")
            assert ei.value.code() == grpc.StatusCode.PERMISSION_DENIED
        # an unscoped read key lists all aliases
        as_(reader)
        assert set(s.listed()) == {"live"}
    finally:
        s.stop()


def test_a_key_limited_to_a_collection_is_not_widened(tmp_path):
    """A key limited to a real collection name does not gain an alias's targets."""
    s = Srv(tmp_path, auth=True)
    admin = s.server.create_key("admin")
    limited = s.server.create_key("write", "a")
    s.start(admin)
    try:
        s.create("a")
        s.create("b")
        s.alias("live", "b")
        s.token = limited
        assert s.get("a").WhichOneof("result") == "info"
        with pytest.raises(grpc.RpcError):
            s.get("b")
        with pytest.raises(grpc.RpcError):
            s.get("live")
    finally:
        s.stop()


def test_backup_copies_the_aliases(tmp_path):
    import subprocess

    s = Srv(tmp_path)
    s.start()
    s.create("a")
    s.alias("live", "a")
    out = tmp_path / "bk"
    bin_dir = find_server_binary().parent
    r = subprocess.run(  # noqa: S603
        [
            str(bin_dir.parent / "storage" / "ombra_backup"),
            "--data-dir",
            str(s.server.data_dir),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    s.stop()
    assert r.returncode == 0, r.stderr
    db = sqlite3.connect(str(out / "catalog.db"))
    assert db.execute("SELECT name, target FROM aliases").fetchall() == [("live", "a")]
    db.close()
    shutil.rmtree(out)


@pytest.mark.openfhe
def test_client_pinned_to_the_old_collection_while_the_alias_moves(tmp_path):
    """A client that opened the alias keeps working on the old collection after a switch (inserts
    land there); opening the alias again follows the switch. The migration runs against the server
    (rebuild mode); verify needs a search, which the CPU server cannot run, so the switch is
    forced here."""
    pytest.importorskip("openfhe")
    from ombra.errors import AliasConflictError
    from ombra.migrate import Migration

    D = 384
    x = np.random.default_rng(3).normal(size=(6, D))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    s = Srv(tmp_path)
    s.server.openfhe_version = None  # the real version, from the installed openfhe
    s.start()
    client = s.server.client()
    try:
        client.create_collection(
            "docs",
            x,
            [f"i{i}" for i in range(6)],
            key_file=tmp_path / "docs.key",
            passphrase="old",
            preset="fast",
            n_clusters=1,
            scrypt_params=(2**4, 8, 1),
            documents=[f"d{i}" for i in range(6)],
        )
        client.create_alias("live", "docs")
        assert (
            client.open_collection("live", key_file=tmp_path / "docs.key", passphrase="old")
            == "docs"
        )
        m = Migration.plan(
            client,
            "docs",
            source_key_file=tmp_path / "docs.key",
            target_key_file=tmp_path / "docs.m1.key",
            source_passphrase="old",
            target_passphrase="new",
            state_path=tmp_path / "state.json",
            alias="live",
            scrypt_params=(2**4, 8, 1),
        )
        m.start()
        m.backfill()
        assert client.get_collection("docs.m1").vector_count == 6
        # the switch needs a passed verify, which needs a search: the CPU server cannot search
        with pytest.raises(Exception, match="phase"):
            m.switch()
        assert "now points at 'docs.m1'" in m.switch(force=True)
        assert [(a.name, a.target, a.previous) for a in client.list_aliases()] == [
            ("live", "docs.m1", "docs")
        ]
        # the open collection is pinned: an insert under either name goes to the old one
        y = np.random.default_rng(4).normal(size=(1, D))
        y /= np.linalg.norm(y)
        client.insert("live", y, ["new"], documents=["dn"])
        assert client.get_collection("docs").vector_count == 7
        assert client.get_collection("docs.m1").vector_count == 6
        # a second process moving the alias under us loses the compare-and-swap
        with pytest.raises(AliasConflictError):
            client.switch_alias("live", "docs", "docs")  # it points at docs.m1 now
        # the alias resolves to the new collection for a fresh open
        assert client.get_collection("live").name == "docs.m1"
        assert (
            client.open_collection("live", key_file=tmp_path / "docs.m1.key", passphrase="new")
            == "docs.m1"
        )
        # finish refuses before the drain period, then drops the old collection
        with pytest.raises(Exception, match="drain"):
            m.finish()
        m.finish(force=True)
        assert client.list_collections() == ["docs.m1"]
    finally:
        s.stop()
