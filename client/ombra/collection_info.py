# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""The "collection info" blob, version 1 (decision 0028).

A small encrypted record that tells the application which embedding model a collection was built
for, so a client that follows an alias can check that its embedder matches. It is a record blob
(kind 1) under the reserved id `INFO_ID`, sealed like the probe statistics, so the server sees a
blob of the smallest bucket and nothing else.

Plaintext layout, version 1 (the sealed "vector" bytes): magic "OINF" | u16 version (1) |
u32 length | UTF-8 JSON object | zero padding to a multiple of 4. The JSON object holds only
strings, integers and floats. `ombra-migrate` writes `model` (the label you gave), `source` (the
collection it was built from), `migration` (the number n of `<base>.m<n>`), `created` (unix
seconds) and `preset`; `model` is the key the application reads. Other keys are kept as they are.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from ombra.probe import RESERVED_PREFIX

INFO_ID = RESERVED_PREFIX + b"info"
INFO_MAGIC = b"OINF"
INFO_VERSION = 1
MAX_INFO_BYTES = 4096
_HEAD = struct.Struct("<4sHI")


class InfoError(ValueError):
    """The blob is not a collection info record of a version this client reads."""


def pack_info(info: dict[str, Any]) -> bytes:
    """Serialize `info` to a versioned record (magic `OINF`, JSON body, padded to 4 bytes).

    Raises:
        ValueError: The JSON is above `MAX_INFO_BYTES` (4096).
    """
    raw = json.dumps(info, sort_keys=True, ensure_ascii=False).encode()
    if len(raw) > MAX_INFO_BYTES:
        raise ValueError(f"collection info is above {MAX_INFO_BYTES} bytes")
    out = _HEAD.pack(INFO_MAGIC, INFO_VERSION, len(raw)) + raw
    return out + b"\0" * (-len(out) % 4)


def unpack_info(data: bytes) -> dict[str, Any]:
    """Read a record made by `pack_info`.

    Raises:
        InfoError: The record is truncated, has another magic or has an unsupported version.
    """
    if len(data) < _HEAD.size:
        raise InfoError("collection info is truncated")
    magic, version, n = _HEAD.unpack_from(data)
    if magic != INFO_MAGIC:
        raise InfoError("not a collection info record")
    if version != INFO_VERSION:
        raise InfoError(f"collection info version {version} is not supported")
    if n > MAX_INFO_BYTES or _HEAD.size + n > len(data):
        raise InfoError("collection info is truncated")
    obj = json.loads(data[_HEAD.size : _HEAD.size + n])
    if not isinstance(obj, dict):
        raise InfoError("collection info is not an object")
    return obj
