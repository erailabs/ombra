# Homomorphic encryption

Normal encryption protects data at rest and in transit, but to compute on it the server must decrypt it and then sees everything. Homomorphic encryption removes that step. The server computes on the ciphertext, and the result, once decrypted by the key holder, equals what the same computation on the plain data would have given. Example: the client encrypts 3, the server adds the ciphertext to itself without learning what is inside, and the client decrypts 6.

![The client encrypts 3 and sends the ciphertext c; the server computes c + c without the secret key and sends the result back; the client decrypts it and gets 6](../assets/he-basics.svg)

**Plaintext** is the data before encryption, **ciphertext** the encrypted form. **Evaluating** means computing on ciphertexts, and it is all the server does. Everything is built from additions and multiplications. A dot product is multiplications followed by additions, so a server can score a query against stored vectors without seeing either. This matters because an embedding can be inverted to recover much of the text it came from: the server is trusted only to run the computation, not with the data. Keys: [Keys](keys.md).

## What it costs, and what it cannot do

Computing on ciphertexts is expensive. With `balanced` ($N = {{ preset("balanced", "ring_dim") }}$) a fresh ciphertext is about 0.8 MB (2 polynomials × 3 words × 8 bytes × 16384). So Ombra runs the server on GPUs and packs thousands of values into each ciphertext ([CKKS](ckks.md)). Limits that no setting removes:

- The server cannot compare. There is no `if` on an encrypted number. Ombra returns encrypted scores to the client, which decrypts them and picks the top results.
- A computation can only go a few multiplications deep ([levels](ckks.md#levels-and-rescaling)).
- Results are approximate, with a small error.

## Why HE and not FHE

A scheme is fully homomorphic when it can compute anything, which needs an expensive refresh step called bootstrapping. Ombra does not bootstrap. It uses CKKS with a fixed depth of {{ preset("balanced", "multiplicative_depth") }} multiplications, enough for a dot product and a filter. A scheme with a fixed depth is called leveled, so Ombra uses homomorphic encryption (HE), not fully homomorphic encryption (FHE).
