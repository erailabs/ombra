# Blobs

Records and documents are stored as blobs that the client encrypts.

Magic `{{ const("ombra.documents.MAGIC").decode() }}`, version {{ const("ombra.documents.BLOB_VERSION") }}. Source: `client/ombra/documents.py`. The stored blob is exactly one bucket size (16384, 262144, 2097152 or 16777216 bytes), the smallest that fits.

## Layout

| Size | Field |
| --- | --- |
| 4 | magic `OMBD` |
| 2 | version |
| 12 | nonce of the key wrap |
| 48 | wrapped data key: a fresh 32-byte AES key, wrapped with the master key, plus a 16-byte tag |
| 12 | nonce of the data |
| rest | AES-256-GCM ciphertext and tag of the padded plaintext |

The padded plaintext is a mode byte (1 text, 2 bytes, 3 reference, 4 record), the true length as 8 bytes, the payload and zero padding up to the bucket. The true length and the padding are inside the ciphertext, so the server sees the bucket and nothing finer. A record payload is the vector's byte length, the float32 vector and JSON metadata. A reference payload is JSON with `uri`, `sha256` and `size`, for a document that is larger than the inline threshold of {{ const("ombra.documents.DEFAULT_INLINE_THRESHOLD") }} bytes.

## Compatibility

The associated data binds a blob to its place: the magic, version, kind, collection name and id, and for the body also the whole header. A blob copied to another id, collection or kind fails authentication, as does any changed byte. See [Documents and blobs](../../how-it-works/documents.md).

The server stores a blob as the payload of a [stored blob](server-files.md#stored-blob) and never parses it.
