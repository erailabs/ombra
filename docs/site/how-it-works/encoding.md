# Encoding and column packing

A CKKS ciphertext holds one number per slot (see [CKKS](../concepts/ckks.md)): {{ preset("balanced", "batch_size") }} slots in the balanced preset, {{ preset("fast", "batch_size") }} in fast. Ombra puts one dimension of many vectors in each ciphertext, called column packing.

## Row packing and column packing

Row packing, one vector per ciphertext, needs a sum across slots for each dot product. CKKS can only do that with rotations, about $\log_2 d$ per vector, and a rotation is a key switch, the most expensive operation in the scheme.

Column packing turns the layout by 90 degrees. Take $S$ vectors $v_0, \dots, v_{S-1}$ with $d$ values each, where $S$ is the number of slots. Ciphertext $a_j$ holds dimension $j$ of all of them:

$$a_j[s] = v_s[j] \qquad 0 \le j < d,\ 0 \le s < S$$

The $d$ ciphertexts $a_0, \dots, a_{d-1}$ are one block holding $S$ vectors. For a query $q$, ciphertexts $\hat q_j$ hold $q_j$ in every slot, and multiplying slot by slot and summing over $j$ gives every dot product at once:

$$\text{score}[s] = \sum_{j=0}^{d-1} \hat q_j[s] \cdot a_j[s] = \langle q, v_s \rangle$$

Only multiplications and additions, no rotations. [Query expansion](query-expansion.md) makes the $\hat q_j$ from one ciphertext; [encrypted search](encrypted-search.md) does the multiply-and-add. Vectors are normalized to unit length before encryption, so the scores are cosine similarities.

## What a block looks like in practice

A balanced block holds up to {{ preset("balanced", "batch_size") }} vectors; at $d = 768$ it is 768 ciphertexts. Unused slots are zero and cost the same as full ones, so small clusters are expensive per vector. A cluster of $n$ vectors uses $\lceil n / S \rceil$ blocks.

A fresh balanced column has {{ preset("balanced", "multiplicative_depth") + 1 }} towers of $N = {{ preset("balanced", "ring_dim") }}$ 64-bit words in each of two polynomials, so on the GPU it takes {{ 2 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") * 8 // 1024 }} KiB. A block of 768 columns is about {{ 2 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") * 8 * 768 // 1024 // 1024 }} MiB for {{ preset("balanced", "batch_size") }} vectors, around {{ 2 * (preset("balanced", "multiplicative_depth") + 1) * preset("balanced", "ring_dim") * 8 * 768 // preset("balanced", "batch_size") // 1024 }} KiB per vector, against 3 KiB for 768 plain float32 values. Stored sizes: [Performance](../performance/index.md#sizes).

The query travels as one ciphertext that uses $2d$ of its $N$ coefficients, so $d \le N/2$: {{ preset("fast", "ring_dim") // 2 }} for fast, {{ preset("balanced", "ring_dim") // 2 }} for balanced. The client accepts the dimensions {{ const("ombra.presets.SUPPORTED_DIMENSIONS") }} and refuses others.

A filter field with $V$ possible values adds $V$ columns, one per value, holding 1 in the slots of vectors with that value and 0 elsewhere. A block then has $D = d + \sum V$ columns, and search cost grows with $D$ ([filters](filters.md)).

## How the client builds a column

The client takes value $j$ of each vector in the block, pads with zeros up to the slot count, sets any value smaller than $10^{-9}$ to zero (OpenFHE refuses denormal-sized numbers), and passes the list to OpenFHE's `MakeCKKSPackedPlaintext` and `Encrypt`. Ombra only decides which number goes in which slot. A middle layout with a few dimensions per ciphertext would bring rotations back for each dot product; column packing does them once per query, during expansion.

Code: [`core/include/ombra/core/column_search.hpp`](https://github.com/erailabs/ombra/blob/main/core/include/ombra/core/column_search.hpp), [`core/kernels/column_search.cu`](https://github.com/erailabs/ombra/blob/main/core/kernels/column_search.cu), and `Collection._columns` and `KeyKit.encrypt_column` in [`client/ombra/collection.py`](https://github.com/erailabs/ombra/blob/main/client/ombra/collection.py).
