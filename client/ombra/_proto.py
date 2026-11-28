# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Protobuf (and gRPC) modules of ombra.v1, generated at first use from the packaged .proto.

The package ships `resources/ombra.proto`, a copy of proto/ombra/v1/ombra.proto (a test checks
that they are equal). The modules are generated with `grpc_tools` into a temporary directory, under
the Python package name `ombra_api` so they cannot collide with the client package `ombra`; the
wire names (package ombra.v1) are unchanged. Needs the `local` or `grpc` extra (grpcio-tools).
"""

from __future__ import annotations

import atexit
import importlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

_PROTO = Path(__file__).resolve().parent / "resources" / "ombra.proto"
_MODULES = ("ombra_api.v1.ombra_pb2", "ombra_api.v1.ombra_pb2_grpc")


RECORD_PART = 4_000_000  # below LIMIT_MAX_CHUNK_BYTES (4 MiB)


def record_parts(column: bytes) -> list[bytes]:
    """A record column cut into parts that each fit one message."""
    return [column[o : o + RECORD_PART] for o in range(0, len(column), RECORD_PART)]


def document_parts(columns: Any) -> list[tuple[int, bytes]]:
    """(class, part) of the document columns of a block, classes ascending, each column cut into
    parts that each fit one message. `columns` is the block's `document_columns` (or a dict of
    class -> column)."""
    items = (
        sorted(columns.items())
        if isinstance(columns, dict)
        else sorted((c.doc_class, c.column) for c in columns)
    )
    return [(cls, part) for cls, col in items for part in record_parts(col)]


def insert_requests(pb: Any, blocks: Any) -> Any:
    """The Insert request stream of whole `InsertBlock`s: per block one header, then its columns,
    one per message, then its record column (if any) in parts, then its document columns in parts
    (the server's receive limit is
    16 MiB, so no message holds a whole block).
    A generator, so a `Replayable` can call it again for a retry; the blocks must stay the same
    objects (the retry sends the same bytes, which is what makes a repeated Insert a no-op)."""
    for b in blocks:
        header = pb.InsertHeader(
            collection=b.collection, cluster_id=b.cluster_id, ids=b.ids, is_center=b.is_center
        )
        yield pb.InsertRequest(api_version=b.api_version, header=header)
        for col in b.columns:
            yield pb.InsertRequest(api_version=b.api_version, column=col)
        for part in record_parts(b.record_column):  # after the columns; none if the block has none
            yield pb.InsertRequest(api_version=b.api_version, record_column=part)
        for cls, part in document_parts(b.document_columns):  # after the record column
            doc = pb.DocumentColumnPart(doc_class=cls, data=part)
            yield pb.InsertRequest(api_version=b.api_version, document_column=doc)


def load_stubs(*, grpc_module: bool = True) -> tuple[Any, Any | None]:
    """Return (messages module, grpc module or None). Generated once per process."""
    if _MODULES[0] in sys.modules and (not grpc_module or _MODULES[1] in sys.modules):
        return sys.modules[_MODULES[0]], sys.modules.get(_MODULES[1])
    try:
        from grpc_tools import protoc
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError(
            "grpcio-tools is needed to build the API stubs: pip install 'ombra[local]' "
            "or 'ombra[grpc]'"
        ) from exc
    out = Path(tempfile.mkdtemp(prefix="ombra-proto-"))
    atexit.register(shutil.rmtree, out, ignore_errors=True)
    api = out / "ombra_api" / "v1"
    api.mkdir(parents=True)
    shutil.copy(_PROTO, api / "ombra.proto")
    args = ["protoc", f"-I{out}", f"--python_out={out}"]
    if grpc_module:
        args.append(f"--grpc_python_out={out}")
    code = protoc.main([*args, str(api / "ombra.proto")])
    if code != 0:
        raise RuntimeError("protoc failed on the packaged ombra.proto")
    sys.path.insert(0, str(out))
    pb = importlib.import_module(_MODULES[0])
    pbg = importlib.import_module(_MODULES[1]) if grpc_module else None
    return pb, pbg
