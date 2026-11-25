# Key file

Holds the client's OpenFHE secret key and the master key for documents, and with [private retrieval](../../how-it-works/private-retrieval.md) the retrieval key pair, protected by a passphrase. It has its own magic because the [envelope](envelope.md) has no secret-key kind.

Magic `{{ const("ombra.keyfile.MAGIC").decode() }}`, version {{ const("ombra.keyfile.KEYFILE_VERSION") }}, or {{ const("ombra.keyfile.KEYFILE_VERSION_PIR") }} when the file holds the retrieval key pair. Source: `client/ombra/keyfile.py`.

## Layout

Sizes are in bytes.

- 4: magic `OMBK`
- 2: version
- 1: key derivation function: 1 is scrypt
- 4, 4, 4: scrypt $n$, $r$, $p$
- 16: salt
- 12: AES-GCM nonce
- rest: AES-256-GCM ciphertext and tag

In version 1 the plaintext is the 32-byte master key followed by the serialized secret key.

In version 2 the plaintext is:

Sizes are in bytes.

- 32: master key
- 4: length of the secret key, u32
- that length: serialized CKKS secret key
- rest: retrieval key pair

The retrieval key pair starts with magic `OPKB`, a u16 version (1) and a u32 length, then the serialized BFV secret key of that length and the BFV public key. A reader refuses a version 2 body shorter than its length fields, a length past the end, or an empty retrieval key pair.

A file without a retrieval key pair is still written as version 1. A client that only knows version 1 refuses version 2. Everything before the ciphertext is the GCM associated data, so any changed header byte fails decryption. The default scrypt cost is $n = {{ const("ombra.keyfile.DEFAULT_SCRYPT")[0] }}$, $r = {{ const("ombra.keyfile.DEFAULT_SCRYPT")[1] }}$, $p = {{ const("ombra.keyfile.DEFAULT_SCRYPT")[2] }}$, about 128 MiB of memory. When reading, the client caps $n$, $r$ and $p$ so that a hostile file cannot make it allocate gigabytes.

## Compatibility

The client reads both versions. If you lose this file or its passphrase, the data is lost: there is no recovery path, by design. [`ombra_verify`](../cli/ombra-verify.md) checks header and lengths but cannot find a flipped payload byte, because there is no checksum; decryption does.
