# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Key upload preflight with the CPU server and the real Python client.

The server compares an uploaded crypto context with the preset's own before OpenFHE reads it
(server/search/src/context_preflight.cpp). A context with one byte changed, and the context of the
other preset, must be refused with a clear message. The valid upload path is covered by
tests/test_server_cpu.py. Synthetic data only; the refusal comes at the key upload, so no vector is
encrypted.
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

import openfhe as ofhe  # noqa: E402
from ombra import Client  # noqa: E402
from ombra.collection import CollectionError, KeyKit  # noqa: E402
from ombra.file_format import Kind, write_envelope  # noqa: E402
from ombra.params import make_context  # noqa: E402

REFUSAL = "not the context of the collection's preset"
ERROR_INVALID_ARGUMENT = 4


def _vectors() -> np.ndarray:
    x = np.random.default_rng(3).normal(size=(8, 384))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _create_with_context(monkeypatch, tmp_path, make_payload):
    """Create a collection whose uploaded context payload is make_payload(kit); return the error."""
    original = KeyKit.key_files

    def key_files(self, pb):
        files = original(self, pb)
        payload = make_payload(self)
        files[pb.KEY_KIND_CONTEXT] = write_envelope(
            Kind.CONTEXT, self.derived.preset, self.openfhe_version, payload
        )
        return files

    monkeypatch.setattr(KeyKit, "key_files", key_files)
    server = ServerProcess(tmp_path)
    with server:
        client: Client = server.client()
        with pytest.raises(CollectionError) as caught:
            client.create_collection(
                "tampered",
                _vectors(),
                [f"v{i}" for i in range(8)],
                key_file=tmp_path / "t.key",
                passphrase="pw",  # noqa: S106
                preset="fast",
                n_clusters=1,
                scrypt_params=(2**4, 8, 1),
            )
        return caught.value


def test_context_with_one_changed_byte_is_refused(monkeypatch, tmp_path) -> None:
    def tampered(kit) -> bytes:
        raw = bytearray(ofhe.Serialize(kit.cc, ofhe.BINARY))
        raw[len(raw) // 2] ^= 0x01
        return bytes(raw)

    err = _create_with_context(monkeypatch, tmp_path, tampered)
    assert err.code == ERROR_INVALID_ARGUMENT
    assert REFUSAL in err.message


def test_context_of_another_preset_is_refused(monkeypatch, tmp_path) -> None:
    def other(kit) -> bytes:
        _, cc = make_context("m0_depth2")  # the collection is the compact preset ("fast")
        return ofhe.Serialize(cc, ofhe.BINARY)

    err = _create_with_context(monkeypatch, tmp_path, other)
    assert err.code == ERROR_INVALID_ARGUMENT
    assert REFUSAL in err.message
