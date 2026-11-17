# Parameters and security level

You pick a preset by name and a vector dimension. You never pick a ring size or a modulus. Every number below is read from `core/params/ckks_presets.json`, the file that both the Python client and the server load, so this table cannot drift from the code.

| | Fast | Balanced (default) |
| --- | --- | --- |
| Security level | {{ preset("fast", "security_level") }} | {{ preset("balanced", "security_level") }} |
| Ring dimension $N$ | {{ preset("fast", "ring_dim") }} | {{ preset("balanced", "ring_dim") }} |
| Slots per ciphertext | {{ preset("fast", "batch_size") }} | {{ preset("balanced", "batch_size") }} |
| Multiplicative depth | {{ preset("fast", "multiplicative_depth") }} | {{ preset("balanced", "multiplicative_depth") }} |
| First modulus (bits) | {{ preset("fast", "first_mod_size") }} | {{ preset("balanced", "first_mod_size") }} |
| Scaling modulus (bits) | {{ preset("fast", "scaling_mod_size") }} | {{ preset("balanced", "scaling_mod_size") }} |
| Scaling technique | {{ preset("fast", "scaling_technique") }} | {{ preset("balanced", "scaling_technique") }} |
| Key switching | {{ preset("fast", "key_switch_technique") }} | {{ preset("balanced", "key_switch_technique") }} |
| Key-switching digits | {{ preset("fast", "num_large_digits") }} | OpenFHE default |
| Filter fields $F_{max}$ | {{ fmax("fast") }} | {{ fmax("balanced") }} |

The filter penalty weight is $\lambda = {{ const("ombra.presets.FILTER_LAMBDA") }}$. $F_{max}$ is the most filter fields a preset can carry: a decrypted slot holds a score plus $\lambda$ times the number of matching fields, and that sum must stay inside the range the last modulus can represent. See [Filters](../how-it-works/filters.md).

## The 128-bit level

Both presets declare `HEStd_128_classic`: at least 128 bits of security against the best known classical attacks on the lattice problem, using the tables of the HomomorphicEncryption.org standard. For a given ring dimension the standard caps the size of the full modulus, written $\log_2 QP$, where $Q$ is the chain of ciphertext moduli and $P$ is the extra modulus used for key switching.

We know $Q$ from the preset. It is the first modulus plus one scaling modulus per level:

$$\log_2 Q = \text{first} + \text{depth} \times \text{scaling}$$

For Fast that is {{ preset("fast", "first_mod_size") }} + {{ preset("fast", "multiplicative_depth") }} × {{ preset("fast", "scaling_mod_size") }} = {{ preset("fast", "first_mod_size") + preset("fast", "multiplicative_depth") * preset("fast", "scaling_mod_size") }} bits. For Balanced it is {{ preset("balanced", "first_mod_size") }} + {{ preset("balanced", "multiplicative_depth") }} × {{ preset("balanced", "scaling_mod_size") }} = {{ preset("balanced", "first_mod_size") + preset("balanced", "multiplicative_depth") * preset("balanced", "scaling_mod_size") }} bits.

We do not give a number for $P$. The preset file does not contain it: OpenFHE chooses the primes of $P$ itself, and the Python bindings do not expose them. What we can say is that the check below counts $P$ and that the presets pass it.

## Where the floor is enforced

The 128-bit minimum is checked in three places, so that a bad preset fails early instead of running insecurely.

- `client/ombra/params.py` refuses any preset whose security level is not one of the 128-bit or higher levels, including `HEStd_NotSet`, before an OpenFHE object is created.
- `client/ombra/presets.py` runs that check for every preset a collection is created with, and refuses a dimension that does not fit the ring.
- `core/include/ombra/core/security_floor.hpp` checks the context itself on the C++ side. It compares $\log_2 QP$, special primes included, against the standard's table for the ring dimension, and refuses a ring dimension that is not in the table. The server session and the core entry points call it. A context declared at 192 or 256 bits is held to the same 128-bit limit.

## What the server checks on a key upload

The crypto context is data from the client, and OpenFHE would read it with a parser that was not written for hostile input. So the server checks the raw bytes before OpenFHE reads any of them.

- **Context.** The server builds the context of the collection's preset itself, from the preset file, and keeps its serialization. An uploaded context must be byte for byte equal to it, or to the same context as the Python client writes it (the two writers differ in seven version bytes). Any other context is refused, whatever it claims, with the message "the uploaded crypto context is not the context of the collection's preset" and error code `ERROR_INVALID_ARGUMENT`. A context that passes was therefore made from the preset's own parameters.
- **Evaluation keys.** The coefficients depend on the secret key, so a key file cannot be compared as a whole. The rest of the file can: it is structure that cereal, OpenFHE's reader, trusts. That is the length and count of every list, type and version words, the names of polymorphic types, the moduli, the ring dimension, the length of the key tag, the number of keys and the rotation index of each key. cereal allocates memory from such a length, so one damaged length could take the server down. For each preset the server holds a template of an EvalMult key file and of an automorphism key file, learned from eight independent key generations of the real client. Before OpenFHE reads a key file, every byte of it must equal the template, except the coefficient words of the key polynomials, which may be anything. The key tag (32 lowercase hex characters) must be the same everywhere in the file. An EvalMult file holds one key. An automorphism file holds 1 to $\log_2 N$ keys, with the rotation indices $N/2^k + 1$ that the client sends, in ascending order; its size tells how many keys to expect. A file that fails is refused with the offset of the first byte that differs, and OpenFHE never sees it. Coefficient words are still read by OpenFHE, so the fuzz target `keys` changes them too (see `fuzz/README.md`). A template is a learned file: `tests/test_openfhe_key_templates.py` rebuilds it with the client and fails when the client, the preset or OpenFHE changes it.

The checks that run after OpenFHE has read the context stay: the $\log_2 QP$ floor above, and the comparison of ring dimension, moduli and depth with the preset.

Presets come only from the preset file. No code path takes a hand-picked ring size or modulus.

!!! deep-dive "What the level does not cover"
    The level is a statement about the lattice problem for the chosen $N$ and $\log_2 QP$, with a ternary secret key. It does not cover side channels, a compromised client, or anything the server learns from access patterns, which [What the server learns](what-the-server-learns.md) lists. CKKS also adds a small random error to every result, and decrypted values should not be shown to anyone who also holds the ciphertext, see the [threat model](threat-model.md).
