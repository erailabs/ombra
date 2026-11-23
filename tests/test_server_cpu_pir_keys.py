# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Private-retrieval public keys on the CPU server, over gRPC with real OpenFHE keys.

One UploadKeys stream is one key set: the three search kinds or the three retrieval kinds. The
retrieval files are checked against the preset `pir_bfv_n8192` (not the collection's) and stored
beside the search keys. Synthetic data only; a small retrieval shape keeps key generation quick.
"""

from __future__ import annotations

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

from ombra import pir  # noqa: E402
from ombra.file_format import Kind, write_envelope  # noqa: E402

NAME = "pir_keys"
D = 384
CHUNK = 1 << 20


def _vectors() -> np.ndarray:
    x = np.random.default_rng(5).normal(size=(8, D))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _upload(t, collection: str, files: dict[int, bytes]):
    """One UploadKeys stream; files by proto kind number, each in chunks of CHUNK bytes."""

    def chunks():
        first = True
        for kind, data in files.items():
            for off in range(0, max(len(data), 1), CHUNK):
                part = data[off : off + CHUNK]
                yield t.pb.UploadKeysChunk(
                    api_version=1,
                    collection=collection if first else "",
                    kind=kind,
                    offset=off,
                    data=part,
                    last=off + CHUNK >= len(data),
                )
                first = False

    return t.stub.UploadKeys(chunks())


def _key_dir_count(server: ServerProcess) -> int:
    return len(list((server.data_dir / "keys").iterdir()))


def test_pir_key_upload(tmp_path) -> None:
    server = ServerProcess(tmp_path)
    with server:
        client = server.client()
        client.create_collection(
            NAME,
            _vectors(),
            [f"v{i}" for i in range(8)],
            key_file=tmp_path / "k.key",
            passphrase="pw",  # noqa: S106
            preset="fast",
            n_clusters=1,
            scrypt_params=(2**4, 8, 1),
        )
        t = client._core._t
        pb = t.pb
        assert _key_dir_count(server) == 3  # the search keys the client uploaded

        shape = pir.make_shape(200, 4, 256, b"0123456789abcdef")
        files = pir.PirClient(shape).key_files(t.openfhe_version)
        pir_set = {
            pb.KEY_KIND_PIR_CONTEXT: files["pir_context"],
            pb.KEY_KIND_PIR_EVAL_MULT: files["pir_eval_mult"],
            pb.KEY_KIND_PIR_AUTOMORPHISM: files["pir_automorphism"],
        }

        # A partial set is refused, like a partial search set; nothing is stored.
        part = {k: v for k, v in pir_set.items() if k != pb.KEY_KIND_PIR_AUTOMORPHISM}
        assert _upload(t, NAME, part).error.code == pb.ERROR_INVALID_ARGUMENT
        assert _key_dir_count(server) == 3

        # Search and retrieval kinds cannot share an upload, in either order.
        search_ctx = {pb.KEY_KIND_CONTEXT: b"x"}
        mixed = {**pir_set, **search_ctx}
        assert _upload(t, NAME, mixed).error.code == pb.ERROR_INVALID_ARGUMENT
        mixed = {**search_ctx, **pir_set}
        assert _upload(t, NAME, mixed).error.code == pb.ERROR_INVALID_ARGUMENT
        assert _key_dir_count(server) == 3

        # A retrieval file in the search preset's envelope is refused.
        wrong = dict(pir_set)
        wrong[pb.KEY_KIND_PIR_CONTEXT] = write_envelope(
            Kind.CONTEXT, "m0_compact_n8192", t.openfhe_version, b"x"
        )
        assert _upload(t, NAME, wrong).error.code == pb.ERROR_PRESET_MISMATCH
        assert _key_dir_count(server) == 3

        # A context above the per-file limit is refused while it streams.
        big = dict(pir_set)
        big[pb.KEY_KIND_PIR_CONTEXT] = write_envelope(
            Kind.CONTEXT, pir.PIR_PRESET, t.openfhe_version, bytes(2 << 20)
        )
        assert _upload(t, NAME, big).error.code == pb.ERROR_SIZE_LIMIT

        # The full retrieval set is stored beside the search keys.
        assert _upload(t, NAME, pir_set).error.code == pb.ERROR_UNSPECIFIED
        assert _key_dir_count(server) == 6
        assert client.get_collection(NAME).vector_count == 8

        # A second upload replaces the three files.
        files2 = pir.PirClient(shape).key_files(t.openfhe_version)
        again = {
            pb.KEY_KIND_PIR_CONTEXT: files2["pir_context"],
            pb.KEY_KIND_PIR_EVAL_MULT: files2["pir_eval_mult"],
            pb.KEY_KIND_PIR_AUTOMORPHISM: files2["pir_automorphism"],
        }
        assert _upload(t, NAME, again).error.code == pb.ERROR_UNSPECIFIED
        assert _key_dir_count(server) == 6

        # Everything survives a restart.
        server.restart(kill=True)
        assert _key_dir_count(server) == 6
