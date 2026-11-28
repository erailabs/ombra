# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Helper process of the local server for private retrieval: runs `PirServerReference` (which
needs openfhe-python) in a process of its own, because the local server process holds the
compiled OpenFHE of `ombra._native` and the two copies are never loaded into one process.

Input on stdin and output on stdout are frames (u64 little-endian length, then bytes). It reads
public material only: the serialized context, evaluation keys, the record column and the query
ciphertexts. Modes (first frame):
  b"tag": context, mult key, automorphism key -> one frame, the key tag (b"" if the two keys do
          not belong to one key pair)
  b"answer": context, mult key, automorphism key, column, m (u64), seed, queries... -> one frame
          per bucket answer
  b"answer_docs": the same with a document column -> one frame per answer, plane-major
Run as `python -m ombra._pir_worker`. Never prints anything else.
"""

from __future__ import annotations

import struct
import sys
from typing import BinaryIO

_LEN = struct.Struct("<Q")


def write_frames(out: BinaryIO, frames: list[bytes]) -> None:
    for f in frames:
        out.write(_LEN.pack(len(f)))
        out.write(f)
    out.flush()


def read_frames(data: bytes) -> list[bytes]:
    frames, pos = [], 0
    while pos < len(data):
        (n,) = _LEN.unpack_from(data, pos)
        pos += _LEN.size
        if n > len(data) - pos:
            raise ValueError("truncated frame")
        frames.append(data[pos : pos + n])
        pos += n
    return frames


def _tag(context: bytes, mult: bytes, auto: bytes) -> bytes:
    import openfhe as ofhe

    cc = ofhe.DeserializeCryptoContextString(context, ofhe.BINARY)
    ofhe.ClearEvalMultKeys()
    ofhe.CryptoContext.ClearEvalAutomorphismKeys()
    ofhe.DeserializeEvalMultKeyString(mult, ofhe.BINARY)
    ofhe.DeserializeEvalAutomorphismKeyString(auto, ofhe.BINARY)
    mult_tags = set(ofhe.CryptoContext.GetAllEvalMultKeys())
    auto_tags = set(ofhe.CryptoContext.GetAllEvalAutomorphismKeys())
    if len(mult_tags) != 1 or mult_tags != auto_tags:
        return b""
    del cc
    return next(iter(mult_tags)).encode()


def main() -> int:
    frames = read_frames(sys.stdin.buffer.read())
    mode = frames[0]
    if mode == b"tag":
        out = [_tag(*frames[1:4])]
    elif mode == b"answer":
        from ombra.pir_reference import PirServerReference

        ctx, mult, auto, column, m, seed, *queries = frames[1:]
        server = PirServerReference(ctx, mult, auto, column, _LEN.unpack(m)[0], seed)
        out = server.answer(queries)
    elif mode == b"answer_docs":
        from ombra.pir_reference import PirDocumentsReference

        ctx, mult, auto, column, m, seed, *queries = frames[1:]
        out = PirDocumentsReference(ctx, mult, auto, column, _LEN.unpack(m)[0], seed).answer(
            queries
        )
    else:
        return 2
    write_frames(sys.stdout.buffer, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
