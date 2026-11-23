# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The client's private record retrieval against the real CPU `ombra_server`.

Search needs a GPU, so this test takes the block layout from ListBlocks and GetBlock (the same ids
in slot order that a search answer carries) and calls `private_retrieval.retrieve` directly. It
checks the stream order of the real server (Info first, `n_queries = 0`), the key upload on the
first call, and that the opened rows equal the inserted vectors. Synthetic data only.
"""

from __future__ import annotations

import random

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")
pytest.importorskip("openfhe")

from server_harness import ServerProcess, find_server_binary  # noqa: E402

pytestmark = pytest.mark.openfhe

if find_server_binary() is None:
    pytest.skip(
        "ombra_server (CPU build) not found: run `make build-cpu` or set OMBRA_SERVER_BIN",
        allow_module_level=True,
    )

from ombra import private_retrieval as pr  # noqa: E402

D = 384
N = 24
NAME = "cpu_private"


def _layout(t) -> dict[tuple[int, int], list[bytes]]:
    """(cluster, block index) -> ids in slot order, blocks in creation order."""
    pb, stub = t.pb, t.stub
    blocks = stub.ListBlocks(pb.ListBlocksRequest(api_version=1, collection=NAME)).blocks
    out: dict[tuple[int, int], list[bytes]] = {}
    per: dict[int, int] = {}
    for b in blocks:
        if b.cluster_id != 0:
            continue  # the center block (one cluster only)
        parts = stub.GetBlock(pb.GetBlockRequest(api_version=1, collection=NAME, segment=b.segment))
        header = next(p.header for p in parts if p.WhichOneof("part") == "header")
        bi = per.get(b.cluster_id, 0)
        per[b.cluster_id] = bi + 1
        out[(b.cluster_id, bi)] = [bytes(i) for i in header.ids]
    return out


def test_private_retrieval_against_the_cpu_server(tmp_path) -> None:
    rng = np.random.default_rng(11)
    x = rng.normal(size=(N, D))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    ids = [f"r{i}" for i in range(N)]
    with ServerProcess(tmp_path) as server:
        client = server.client()
        client.create_collection(
            NAME,
            x,
            ids,
            key_file=tmp_path / "k.key",
            passphrase="pw",  # noqa: S106
            preset="fast",
            n_clusters=1,
            scrypt_params=(2**4, 8, 1),
            private_retrieval=True,
        )
        client.insert(NAME, x[:4] * -1.0, [f"s{i}" for i in range(4)])  # a second block
        col = client._core._get(NAME)[0]
        layout = _layout(client._core._t)
        assert len(layout) >= 2
        expect = {i.encode(): v for i, v in zip(ids, x, strict=True)}
        expect |= {f"s{i}".encode(): -x[i] for i in range(4)}
        wanted = [
            pr.Wanted(ident, c, b, slot)
            for (c, b), block_ids in layout.items()
            for slot, ident in enumerate(block_ids)
            if ident in expect
        ][::3]
        assert any(w.block == 1 for w in wanted)
        for _ in range(2):  # the first call uploads the retrieval keys, the second reuses them
            rng = random.Random(3)  # noqa: S311  # a fixed fill for a repeatable test
            out = pr.retrieve(
                col, wanted, lambda c, b: layout.get((c, b)), scope=None, m=16, rng=rng
            )
            assert out.asked == 16 and out.filled == 16 - len(wanted)  # the rest are random rows
            assert (out.stale, out.bad_rows) == (0, 0)
            got = {w.ident for w in wanted} & set(out.rows)
            assert len(got) + out.dropped == len(wanted) and out.dropped <= 1
            for ident, (vec, ints) in out.rows.items():
                assert np.allclose(vec, expect[ident], atol=1e-6)
                assert len(ints) == 0
