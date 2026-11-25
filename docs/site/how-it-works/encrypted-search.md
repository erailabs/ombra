# Encrypted search

After [expansion](query-expansion.md) the server holds the query as $D$ ciphertexts $\hat q_j$, each with $q_j$ in every slot. A stored block is $D$ ciphertexts $a_j$ with dimension $j$ of up to {{ preset("balanced", "batch_size") }} vectors ([column packing](encoding.md)). For each block the server computes one ciphertext whose slot $s$ is the score of vector $s$: multiply and add, relinearize, rescale.

## Multiply and add

$$\text{score}[s] = \sum_{j=0}^{D-1} \hat q_j[s] \cdot a_j[s]$$

A ciphertext $(c_0, c_1)$ decrypts as $c_0 + c_1 s$, with $s$ the secret key. The product of $(a_0, a_1)$ and $(b_0, b_1)$ is

$$(a_0 + a_1 s)(b_0 + b_1 s) = a_0 b_0 + (a_0 b_1 + a_1 b_0)\, s + a_1 b_1\, s^2$$

so it has three components, and the sum over $j$ is the componentwise sum of such triples. The server never touches $s$. It combines the polynomials in NTT form, one residue tower at a time ([CKKS](../concepts/ckks.md)), where polynomial multiplication is one modular multiplication per coefficient.

Each block takes 4 modular multiplications and 3 additions per coefficient and dimension, as one kernel launch. In balanced a fresh ciphertext has {{ preset("balanced", "multiplicative_depth") + 1 }} towers and $N = {{ preset("balanced", "ring_dim") }}$, so one block at $D = 768$ costs $768 \cdot 4 \cdot {{ preset("balanced", "multiplicative_depth") + 1 }} \cdot {{ preset("balanced", "ring_dim") }} = {{ 768 * 4 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") }}$ modular multiplications for {{ preset("balanced", "batch_size") }} vectors ({{ 768 * 4 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") // preset("balanced", "batch_size") }} per vector). They are integer operations on 64-bit words, which suit a GPU.

The result is at scale $\Delta^2$ with three components.

## Relinearize

Relinearization turns the three-component ciphertext back into two. It is a key switch: the $s^2$ part is re-expressed in terms of $s$ with a public evaluation key the client uploaded during setup, which hides $s$ the way a public key does. A key switch costs much more than a multiplication, but it is linear, so doing it once on the sum equals doing it on each product. The server relinearizes once per block: at $D = 768$, one key switch against 768 multiplications.

## Rescale

After multiplication the scale is $\Delta^2$, and a second multiplication would overflow the modulus. Rescaling divides by the last modulus $q_\ell$ of the chain, close to $\Delta$, and drops that tower: scale $\Delta^2 \to \Delta^2 / q_\ell \approx \Delta$. In balanced the scaling moduli have {{ preset("balanced", "scaling_mod_size") }} bits and the first has {{ preset("balanced", "first_mod_size") }}, so $\Delta \approx 2^{ {{ preset("balanced", "scaling_mod_size") }} }$, and the ciphertext goes from {{ preset("balanced", "multiplicative_depth") + 1 }} towers to {{ preset("balanced", "multiplicative_depth") }}. The server sends it to the client, which decrypts it and reads one score per slot.

## The level budget

A level is one rescale. `multiplicative_depth` is {{ preset("balanced", "multiplicative_depth") }} in both product presets, and the search uses one, in the rescale. Expansion (adds, subtractions, automorphisms, shifts), multiply-and-add, relinearization and the tombstone penalty (a plaintext addition) use none. A result is at level 1 of {{ preset("balanced", "multiplicative_depth") }}, with one level spare. The core library has a variant that multiplies the scores by a 0/1 mask and rescales again (`GpuSearch` in `core/`), which the server does not use. Filters are part of the query ([filters](filters.md)), so they cost no level either.

!!! deep-dive "How big can a score get"
    A slot that gets too large wraps around and decrypts to nonsense. The client allows a slot value of at most $2^{\,b}$, with $b = \mathtt{first\_mod\_size}-\mathtt{scaling\_mod\_size}-1-3$ (the bound at the lowest level minus a margin of 3 bits). For balanced $b = {{ preset("balanced", "first_mod_size") - preset("balanced", "scaling_mod_size") - 4 }}$, so slots stay below {{ 2 ** (preset("balanced", "first_mod_size") - preset("balanced", "scaling_mod_size") - 4) }}. A score is at most 1, and a filter adds $\lambda = {{ const("ombra.presets.FILTER_LAMBDA") }}$ per matched field. That caps the filter fields at $F_{max} = {{ fmax("balanced") }}$ for balanced and {{ fmax("fast") }} for fast.

## What decides the speed

Per block, the search is one kernel launch for the sum, then a relinearization and a rescale for a group of blocks. [Expansion](query-expansion.md) is the larger server cost. Each GPU thread handles {{ cpp("core/kernels/column_search.cu", "kBlockTile") }} blocks at once, so the query words are read once for all of them. Measured shares and times: TBD, see [Performance](../performance/index.md).

Search only works on CKKS with fixed manual scaling and hybrid key switching, which both presets use; other settings are refused. Every step gives exactly the words OpenFHE's CPU code gives, and the tests compare the two.
