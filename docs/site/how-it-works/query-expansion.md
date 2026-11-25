# Query expansion

[Column packing](encoding.md) needs the query as $D$ ciphertexts, one per dimension, each with $q_j$ in every slot. Sending that would cost $D$ encryptions and about $D$ times the upload. Instead the client sends one ciphertext and the server expands it. The expansion uses no multiplication level.

## The query as a polynomial

A CKKS plaintext is a polynomial of degree below $N$ with $X^N = -1$. The query is encoded so that its coefficients, not its slots, carry the values:

$$p(X) = q_0 + \sum_{j=1}^{d-1} q_j \left( X^j + X^{ -j } \right)$$

Since $X^{ -j } = -X^{N-j}$, coefficient $j$ is $q_j$, coefficient $N-j$ is $-q_j$, and the rest are zero. This symmetric shape makes the slot values real, as OpenFHE's default mode requires. The client computes the slot values with a numpy FFT and hands them to OpenFHE for encoding and encryption.

## One split step

The server needs one constant polynomial per $q_j$. It gets there by repeatedly splitting a polynomial into its even and odd part with an automorphism $\tau_t$, which replaces $X$ by $X^t$ for an odd $t$. On ciphertexts it is a key switch with a public Galois key, so the server computes it without seeing anything. For a node $c$ at depth $l$, with $t = N/2^l + 1$ and nonzero coefficients at multiples of $2^l$:

$$\text{even child} = c + \tau_t(c) \qquad \text{odd child} = \left( c - \tau_t(c) \right) \cdot X^{ -2^l }$$

The automorphism flips the sign of every second coefficient, so the sum keeps one half, doubled, and the difference keeps the other half, doubled. The multiplication by $X^{ -2^l }$ is an exact shift of coefficients.

## The expansion tree

Start with the root $p$ at depth 0 and split down for

$$L = \lceil \log_2 (2d) \rceil$$

levels (`expansion_depth(d)`, the smallest $L$ with $2^L \ge 2d$). Using $2d$ keeps coefficient $N-j$ (holding $-q_j$) out of the residue of leaf $r$, so leaf $r$ holds $2^L q_r$ and nothing else. The tree only creates the branches it needs, so exactly $d$ leaves come out, all at depth $L$. Splits that only produce an even child still cost a key switch, so the number of automorphisms exceeds $d-1$. At $d = 768$ the depth is $L = {{ (2 * 768-1).bit_length() }}$ and the number of automorphisms is the sum over depths of $\min(2^l, d)$:

{% set ns = namespace(total=0) %}{% for l in range((2 * 768-1).bit_length()) %}{% set ns.total = ns.total + ([2 ** l, 768] | min) %}{% endfor %}
$$\sum_{l=0}^{ {{ (2 * 768-1).bit_length()-1 }} } \min(2^l, 768) = {{ ns.total }}$$

All splits at one depth use the same Galois element and shift, so the GPU runs a whole depth in one batch. The client prepares one Galois key per depth, $L$ in total, and uploads them as public evaluation keys. The query dimension is limited to $d \le N/2$.

## Why it uses no level

Multiplying by a number such as $2^{-L}$ costs a level at the next rescale (see [CKKS](../concepts/ckks.md)). Nothing in the tree does that: additions leave scale and level alone, the shift is an exact signed permutation, and the automorphism drops no modulus.

Each leaf is $2^L$ times the value we want. Instead of removing that factor with a multiplication, the client divides by $2^L$ before encrypting (`prescale_query`). Scaling by a power of two is exact in 64-bit floats, so after $L$ doublings the leaf is exactly $q_j$. The norm check on the query happens before the scaling. The noise is not scaled down, and every split doubles what it passes on, so a leaf can carry up to about $2^L$ times the noise of a fresh ciphertext ([accuracy](accuracy.md)).

## Cost

Expansion is the biggest server step when a search touches only a few blocks: at $d = 768$ it needs {{ ns.total }} key switches per query, whatever the number of blocks searched. Measured times: TBD, see [Performance](../performance/index.md). Memory is also large: every leaf is a full ciphertext and the tree keeps two consecutive depths alive, so at $d = 768$ the peak is ${{ 2 * 768 }}$ ciphertexts, about {{ 2 * 768 * 2 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") * 8 // 1024 // 1024 }} MiB of GPU memory per query in balanced.
