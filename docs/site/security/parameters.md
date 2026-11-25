# Parameters and security level

You pick a preset by name and a vector dimension, never a ring size or a modulus. Every number below is read from `core/params/ckks_presets.json`, which the Python client and the server both load.

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

## Choosing a preset

Start with Balanced, the default. Both presets meet the 128-bit level and matched plaintext search in recall in our tests.

- **Balanced** has the best score precision. Pick it when your latency budget has room. There is no higher-precision preset, because Balanced shows no measurable recall loss.
- **Fast** has half the ring dimension and slots. Keys are about 40% smaller, the query upload is half the size, and queries are faster, at the cost of a larger score error. Pick it for interactive search or small keys.

Storage per vector is the same for both. The dimension (384, 768 or 1536) comes from your embedding model. A smaller one is cheaper on every axis, and fewer vectors fit in GPU memory at larger ones. Sizes are on the [Performance](../performance/index.md) page; timings are TBD.

Fast, Decoy and Strict are also the names of the security levels of a collection, a separate choice (Fast there is not the Fast preset). Decoy adds random clusters to each query and Strict reads every cluster, so both cost more than the default. See [Clusters](../how-it-works/clusters.md).

## The 128-bit level

Both presets declare `HEStd_128_classic`: at least 128 bits against the best known classical attacks on the lattice problem, using the HomomorphicEncryption.org tables. For a ring dimension the standard caps the full modulus $\log_2 QP$, where $Q$ is the ciphertext modulus chain and $P$ the extra modulus for key switching.

$\log_2 Q = \text{first} + \text{depth} \times \text{scaling}$. For Fast that is {{ preset("fast", "first_mod_size") }} + {{ preset("fast", "multiplicative_depth") }} × {{ preset("fast", "scaling_mod_size") }} = {{ preset("fast", "first_mod_size") + preset("fast", "multiplicative_depth") * preset("fast", "scaling_mod_size") }} bits, and for Balanced {{ preset("balanced", "first_mod_size") }} + {{ preset("balanced", "multiplicative_depth") }} × {{ preset("balanced", "scaling_mod_size") }} = {{ preset("balanced", "first_mod_size") + preset("balanced", "multiplicative_depth") * preset("balanced", "scaling_mod_size") }} bits. $P$ is not in the preset file (OpenFHE chooses its primes, and the Python bindings do not expose them); the check below counts it, and the presets pass.

## Where the floor is enforced

- `client/ombra/params.py` refuses any preset below 128 bits, including `HEStd_NotSet`, before an OpenFHE object is created.
- `client/ombra/presets.py` runs that check for every preset a collection is created with, and refuses a dimension that does not fit the ring.
- `core/include/ombra/core/security_floor.hpp` checks the context on the C++ side: $\log_2 QP$, special primes included, against the standard's table for the ring dimension, refusing a dimension not in the table. A context declared at 192 or 256 bits is held to the same 128-bit limit. The server session and core entry points call it.

## What the server checks on a key upload

The crypto context comes from the client, and OpenFHE's parser was not written for hostile input, so the server checks the raw bytes before OpenFHE reads any of them.

- **Context.** The server builds the preset's context itself. An uploaded context must be byte for byte equal to it, or to the same context as the Python client writes it (the writers differ in seven version bytes). Otherwise it is refused with `ERROR_INVALID_ARGUMENT`: "the uploaded crypto context is not the context of the collection's preset".
- **Evaluation keys.** The coefficients depend on the secret key, so a key file cannot be compared whole. The rest is structure that cereal, OpenFHE's reader, trusts (list lengths and counts, type and version words, moduli, ring dimension, key tag length, number of keys, rotation indices), and a damaged length could make it allocate too much. For each preset the server holds a template of an EvalMult key file and of an automorphism key file, learned from eight independent key generations of the real client. Every byte of an upload must equal the template except the coefficient words. The key tag (32 lowercase hex characters) must be the same everywhere. An EvalMult file holds one key; an automorphism file holds 1 to $\log_2 N$ keys with rotation indices $N/2^k + 1$ in ascending order. A failing file is refused with the offset of the first differing byte, and OpenFHE never sees it. OpenFHE still reads the coefficient words, so the fuzz target `keys` mutates them (see `fuzz/README.md`). `tests/test_openfhe_key_templates.py` rebuilds the templates and fails when the client, preset or OpenFHE changes them.

After OpenFHE reads the context, the server also checks the $\log_2 QP$ floor and compares ring dimension, moduli and depth with the preset.

Presets come only from the preset file; no code path takes a hand-picked ring size or modulus. The file has a format version (now 2), and each preset may name its scheme (CKKS if absent). It also holds one preset for the exact scheme BFV, used for fetching stored records. It is never a collection preset and passes the same 128-bit check.

!!! deep-dive "What the level does not cover"
    The level is a statement about the lattice problem for the chosen $N$ and $\log_2 QP$, with a ternary secret key. It does not cover side channels, a compromised client, or what the server learns from access patterns ([What the server learns](what-the-server-learns.md)). CKKS also adds a small random error to every result, and decrypted values should not be shown to anyone who also holds the ciphertext, see the [threat model](threat-model.md).
