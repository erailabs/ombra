# Query expansion

[Column packing](encoding.md) needs the query as $D$ ciphertexts, one per dimension, each with $q_j$ in every slot. Sending that would cost $D$ encryptions and about $D$ times the upload. Instead the client sends one ciphertext and the server expands it. The expansion uses no multiplication level.

## The query as a polynomial

A CKKS plaintext is a polynomial of degree below $N$ in $\mathbb{Z}[X]/(X^N + 1)$, with $X^N = -1$. The query is encoded so that its coefficients, not its slots, carry the values:

$$p(X) = q_0 + \sum_{j=1}^{d-1} q_j \left( X^j + X^{ -j } \right)$$

Since $X^{ -j } = -X^{N-j}$, coefficient $j$ is $q_j$, coefficient $N-j$ is $-q_j$, and the rest are zero. This symmetric shape makes the slot values real, as OpenFHE's default mode requires. The client computes the slot values with a numpy FFT and hands them to OpenFHE for encoding and encryption.

## One split step

The server needs one ciphertext per $q_j$, each a constant polynomial (same value in every slot). It gets there by repeatedly splitting a polynomial into its even and odd part with an automorphism.

The automorphism $\tau_t$ replaces $X$ by $X^t$ for an odd $t$. It moves coefficient $i$ to position $it \bmod 2N$, with a sign flip past $N$. On ciphertexts it is a key switch with a public Galois key, so the server computes it without seeing anything. Take $t = N/2^l + 1$ and a polynomial with nonzero coefficients at multiples of $2^l$, $i = 2^l m$. Then $X^{it} = (-1)^m X^i$: the automorphism flips the sign of odd-$m$ coefficients. Adding $c$ and $\tau_t(c)$ keeps the even-$m$ coefficients, doubled; subtracting keeps the odd ones, doubled. For a node $c$ at depth $l$:

$$\text{even child} = c + \tau_t(c) \qquad \text{odd child} = \left( c - \tau_t(c) \right) \cdot X^{ -2^l }$$

The multiplication by $X^{ -2^l }$ moves the odd coefficients down by $2^l$, so both children again sit at multiples of $2^{l+1}$. Each child holds twice what it took over, the factor 2 per level.

## The expansion tree

Start with the root $p$ at depth 0, split with $l = 0$, then both children with $l = 1$, and so on. After $L$ levels each leaf holds the coefficients of one residue modulo $2^L$. For leaf $r$ to be the constant $q_r$:

$$L = \lceil \log_2 (2d) \rceil$$

This is `expansion_depth(d)` in the client and server, the smallest $L$ with $2^L \ge 2d$. Using $2d$ keeps coefficient $N-j$ (holding $-q_j$) out of the residue of $r$.

The tree only creates the branches it needs: a node with label $r$ makes an odd child with label $r + 2^l$ only when $r + 2^l < d$. So exactly $d$ leaves come out, all at depth $L$. For $d = 4$, $N = 16$: $L = 3$, splits use $t = 17, 9, 5$ (the Galois elements the client prepares keys for), and the tree makes 7 automorphisms. Splits that only produce an even child still cost a key switch, so the count exceeds $d-1$. At $d = 768$ the depth is $L = {{ (2 * 768-1).bit_length() }}$ and the number of automorphisms is the sum over depths of $\min(2^l, d)$:

{% set ns = namespace(total=0) %}{% for l in range((2 * 768-1).bit_length()) %}{% set ns.total = ns.total + ([2 ** l, 768] | min) %}{% endfor %}
$$\sum_{l=0}^{ {{ (2 * 768-1).bit_length()-1 }} } \min(2^l, 768) = {{ ns.total }}$$

All splits at one depth use the same Galois element and shift, so the GPU runs a whole depth in one batch.

## Why it uses no level

Multiplying by a number such as $2^{-L}$ costs a level at the next rescale (see [CKKS](../concepts/ckks.md)). Nothing in the tree does that: addition and subtraction at the same level leave scale and level alone, the multiplication by $X^{ -2^l }$ is an exact signed permutation of coefficients, and the automorphism switches the key and adds a little noise but drops no modulus.

Each leaf is $2^L$ times the value we want, and removing that would cost a level. Instead the client divides by $2^L$ before encrypting. Scaling by a power of two changes only the floating-point exponent, so $q \cdot 2^{-L}$ is exact in 64-bit floats, and after $L$ doublings the leaf is exactly $q_j$. That is `prescale_query` in the client. The norm check on the query happens before the scaling, on the real $q$.

Pre-scaling shrinks the signal but not the noise: every split adds key-switch noise and doubles what it passes on, so a leaf can carry up to about $2^L$ times the noise of a fresh ciphertext. See [accuracy](accuracy.md).

## Cost

Expansion is the biggest server step when a search touches only a few blocks. The reason is the key switches: at $d = 768$ there are {{ ns.total }} per query, and the cost does not depend on how many blocks are searched. Measured times: TBD, see [Performance](../performance/index.md).

Memory is also large. Every leaf is a full ciphertext and the tree keeps two consecutive depths alive, so at $d = 768$ the peak is ${{ 2 * 768 }}$ ciphertexts, at {{ 2 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") * 8 // 1024 }} KiB each in balanced about {{ 2 * 768 * 2 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") * 8 // 1024 // 1024 }} MiB of GPU memory per query. Expansion needs one Galois key per depth, $L$ keys in total, uploaded by the client as public evaluation keys. The query dimension is limited to $D \le N/2$.
