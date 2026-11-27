# Envelope

Every ciphertext, crypto context and public evaluation key travels and is stored in an envelope: a header around OpenFHE's binary serialization.

Magic `{{ const("ombra.file_format.MAGIC").decode() }}`. The version is {{ const("ombra.file_format.FORMAT_VERSION") }} for kinds 1 to 3 and {{ const("ombra.file_format.FORMAT_VERSION_SEEDED") }} for kind 4, the seeded ciphertext. A reader refuses a kind that does not match its version.

## Layout

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | magic `OMBR` |
| 4 | 2 | format version |
| 6 | 2 | kind: 1 context, 2 ciphertext, 3 public evaluation key, 4 seeded ciphertext |
| 8 | 2 | preset name length $P$, 1 to {{ const("ombra.file_format.MAX_NAME") }} |
| 10 | $P$ | preset name |
| . | 2 | OpenFHE version length $V$, 1 to {{ const("ombra.file_format.MAX_NAME") }} |
| . | $V$ | OpenFHE version string |
| . | 8 | payload length $L$ |
| . | $L$ | payload: OpenFHE binary serialization (kinds 1 to 3) or the seeded layout below (kind 4). The file ends here. |

There is no kind for a secret key. A reader checks kind, preset name and OpenFHE version, and the payload length against the bytes present before allocating.

## Seeded ciphertext

A seeded ciphertext stores a 32-byte seed in place of the uniformly random half, so it is about half the size. The server expands it when loading a block. Payload of kind 4:

| Size | Field |
| --- | --- |
| 2 | payload version, 1 |
| 2 | generator, 1 is BLAKE2xb |
| 32 | seed, a fresh one from the operating system for each ciphertext |
| 4 | ring dimension $N$ |
| 2 | towers $T$ |
| 4 | level |
| 4 | noise scale degree |
| 4 | slots |
| 8 | scaling factor, IEEE double |
| 2 + $K$ | key tag length $K$ and the key tag |
| $8T$ | moduli, 8 bytes each |
| $8TN$ | the first polynomial, tower by tower, in evaluation form |

A reader refuses an unknown payload version or generator, a size that does not match $N$ and $T$, a word that is not reduced, and trailing bytes.

## Compatibility

A collection may hold both kinds of column. The client writes kind 4 when its compiled module is installed and `OMBRA_SEEDED` is not `0`; without the module it writes kind 2 and refuses to read kind 4.

Sources: `client/ombra/file_format.py`, `core/include/ombra/core/file_envelope.hpp`. The C++ constants `kEnvelopeVersion` ({{ cpp("core/include/ombra/core/file_envelope.hpp", "kEnvelopeVersion") }}) and `kEnvelopeVersionSeeded` ({{ cpp("core/include/ombra/core/file_envelope.hpp", "kEnvelopeVersionSeeded") }}) match the Python ones.
