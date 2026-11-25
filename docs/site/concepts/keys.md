# Keys and who holds them

Ombra uses four kinds of key material. Two never leave the client; the server gets only what it needs to compute.

| Key | What it does | Who holds it |
| --- | --- | --- |
| Secret key | Decrypts. Every other key is made from it. | Client only |
| Public key | Encrypts. Cannot decrypt. | Client (the server never needs it) |
| Multiplication key (EvalMult key) | Lets the server shrink the product of two ciphertexts back to normal size. | Server, uploaded by the client |
| Automorphism keys | Let the server rotate the slots of a ciphertext. | Server, uploaded by the client |

The last two are evaluation keys. They are public and do not help anyone decrypt. With them the client uploads the crypto context, the parameter set (ring dimension, moduli), which contains no key.

The secret key is generated on the client and stays there. No message in the server's API has a field that could carry it. On disk it lives in a passphrase-protected key file. If you lose the file or the passphrase, nobody can recover your data, Ombra included.

## Replacing the keys

The server cannot switch stored vectors to a new key: that needs the old and new secret keys together, and only the client has them. To rotate, the client builds a new collection with a fresh key pair and a fresh document master key and encrypts every vector again from its stored copy (`ombra-migrate --new-keys`, see [Rotating keys](../operations/migration.md#rotating-keys)). Copies of the old data made before the rotation stay readable with the old key file.

## What the evaluation keys are for

**Relinearization.** Multiplying two ciphertexts gives three parts that decrypt as $d_0 + d_1 s + d_2 s^2$, where $s$ is the secret key, and the next operation expects two. Relinearization folds $d_2$ back into the first two. The multiplication key is an encryption of $s^2$ under $s$, so the server can do the fold without knowing $s$.

**Automorphisms.** An automorphism replaces $X$ with $X^k$ in the polynomial, which is how slots rotate (see [CKKS](ckks.md#slots-and-the-ring)). The result is encrypted under $s(X^k)$ instead of $s(X)$, and the automorphism key for that $k$ switches it back. The client generates one key per rotation the search needs and uploads the set.

Both are **key switching**: turning a ciphertext under one key into one under another, with a public key-switching key. Naive key switching adds a lot of noise. The hybrid method used by Ombra's presets splits the key into digits and works in a larger modulus with an extra special modulus, then divides it back out. The `fast` preset uses {{ preset("fast", "num_large_digits") }} digits. More digits mean less noise but larger keys. The preset fixes this.

## What the server can learn from the keys

The evaluation keys are encryptions of the secret key's square and of its rewritten forms. Under the hardness assumption behind CKKS they reveal nothing useful about the secret key. What the server can still observe, such as how many ciphertexts it holds and how often you query, is covered in the security section.
