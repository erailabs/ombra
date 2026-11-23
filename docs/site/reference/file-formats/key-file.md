# Key file

The key file holds the client's OpenFHE secret key and the master key for documents, and on a collection with [private retrieval](../../how-it-works/private-retrieval.md) also the retrieval key pair, protected by a passphrase. It has its own magic because the [envelope](envelope.md) has no secret-key kind.

Magic `{{ const("ombra.keyfile.MAGIC").decode() }}`, version {{ const("ombra.keyfile.KEYFILE_VERSION") }}, or {{ const("ombra.keyfile.KEYFILE_VERSION_PIR") }} when the file holds the retrieval key pair. Source: `client/ombra/keyfile.py`.

## Layout

| Size | Field |
| --- | --- |
| 4 | magic `OMBK` |
| 2 | version |
| 1 | key derivation function: 1 is scrypt |
| 4, 4, 4 | scrypt $n$, $r$, $p$ |
| 16 | salt |
| 12 | AES-GCM nonce |
| rest | AES-256-GCM ciphertext and tag |

In version 1 the plaintext is the 32-byte master key followed by the serialized secret key.

In version 2 the plaintext is:

| Size | Field |
| --- | --- |
| 32 | master key |
| 4 | length of the secret key, u32 |
| that length | serialized CKKS secret key |
| rest | retrieval key pair |

The retrieval key pair starts with magic `OPKB`, a u16 version (1) and a u32 length, then the serialized BFV secret key of that length and the serialized BFV public key. A reader refuses a version 2 body that is shorter than its length fields, a length past the end, or an empty retrieval key pair.

A file without a retrieval key pair is still written as version 1, byte for byte the same as before. A client that only knows version 1 refuses a version 2 file. Everything before the ciphertext, including the scrypt parameters and the salt, is the GCM associated data, so changing any header byte makes decryption fail. The default scrypt cost is $n = {{ const("ombra.keyfile.DEFAULT_SCRYPT")[0] }}$, $r = {{ const("ombra.keyfile.DEFAULT_SCRYPT")[1] }}$, $p = {{ const("ombra.keyfile.DEFAULT_SCRYPT")[2] }}$, which takes about 128 MiB of memory to derive. When reading, the client caps $n$, $r$ and $p$ so that a hostile file cannot make it allocate gigabytes.

## Compatibility

The client reads both versions.


If you lose this file or its passphrase, the data is lost. There is no recovery path, by design.

[`ombra_verify`](../cli/ombra-verify.md) checks the header and lengths of a key file but cannot find a flipped byte in the payload, because the file has no checksum. Decryption with the passphrase does.
