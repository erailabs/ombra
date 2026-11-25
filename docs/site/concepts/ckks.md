# CKKS in simple terms

CKKS is the homomorphic scheme Ombra uses. It works on real numbers and gives approximate answers. Six ideas matter: slots, scale, levels, rescaling, RNS towers and noise.

## Slots and the ring

A ciphertext holds a vector of numbers, called **slots**, and every operation acts on all slots at once: adding or multiplying two ciphertexts works slot by slot. A ciphertext is made of polynomials of degree below $N$ with $X^N = -1$, where $N$ is the **ring dimension**. It carries $N/2$ slots. `fast` has $N = {{ preset("fast", "ring_dim") }}$ and {{ preset("fast", "batch_size") }} slots. `balanced` has $N = {{ preset("balanced", "ring_dim") }}$ and {{ preset("balanced", "batch_size") }} slots.

Replacing $X$ with $X^k$ for an odd $k$ is an **automorphism**. It keeps addition and multiplication intact and moves the data. For $k = 5$ the effect on the slots is a rotation by one place, and powers of 5 rotate by more. Each automorphism needs its own key (see [Keys](keys.md)).

## Scale

CKKS stores real numbers as integers: it multiplies by a fixed factor $\Delta$, the **scale**, and rounds. With $\Delta = 100$, $3.14 \to 314$. Both numbers must carry the same scale to be added. Multiplying squares the scale, which is correct but twice as wide in bits and would soon overflow the modulus. In Ombra, $\Delta$ is about $2^{ {{ preset("balanced", "scaling_mod_size") }} }$ for `balanced` and $2^{ {{ preset("fast", "scaling_mod_size") }} }$ for `fast`.

## Levels and rescaling

**Rescaling** divides a ciphertext by about $\Delta$ and drops that factor from the modulus, bringing the scale back down. Each rescale uses up one **level**: the ciphertext modulus is a fixed chain of numbers, and a rescale removes the last one for good. The number of multiplications you can chain is the **multiplicative depth**, {{ preset("balanced", "multiplicative_depth") }} for both Ombra presets. There is no bootstrapping to refresh levels, so each search is designed to fit in that budget. Additions do not use a level; multiplying by a plain constant does.

The chain for `balanced` is one number of {{ preset("balanced", "first_mod_size") }} bits and {{ preset("balanced", "multiplicative_depth") }} of {{ preset("balanced", "scaling_mod_size") }} bits. `fast` uses {{ preset("fast", "first_mod_size") }} and {{ preset("fast", "scaling_mod_size") }} bits. The scaling numbers are close to $\Delta$ because a rescale divides by one of them. The first is bigger because it must hold the final result. After the last rescale a stored value must stay below $q_0 / 2 \cdot \Delta^{-1}$: for `balanced`, under {{ 2 ** (preset("balanced", "first_mod_size") - preset("balanced", "scaling_mod_size") - 1) }}. Past that, the result wraps around and decrypts to garbage. Ombra keeps scores and filter penalties well below this and derives the number of filter fields it allows from it.

## RNS towers

The total modulus $Q$ is the product of the chain numbers, over 150 bits. The **residue number system (RNS)** avoids big integers by keeping a number as its remainders modulo each chain number, one **tower** per number. Each remainder fits in a 64-bit word, and arithmetic acts on every tower independently, which suits a GPU. Dropping a level means dropping a tower: each polynomial has {{ preset("balanced", "multiplicative_depth") + 1 }} towers when fresh and one fewer after each rescale.

## Noise

Encryption hides a message by adding a small random error, the **noise**. Without it the secret key could be solved for, and it is why answers are approximate. Noise grows with every operation, and rescaling also rounds. It is small against the scale and matters only when it nears it; then the low-order places are garbage. The server cannot measure noise, since it sees only ciphertexts. So the parameters are chosen so the whole computation stays under the noise limit, and accuracy is checked on real data. If noise exceeded the limit, nothing would warn the client.

## What CKKS cannot do

- **Exact answers.** Two scores closer than the error can come back in either order.
- **Comparisons.** There is no test for "greater than", "equal" or "in this set" on an encrypted number, and no `if`. Ombra returns encrypted scores to the client, which decrypts and sorts. Filters add a penalty to the score of rows that do not match, so they drop out when the client ranks.
- **Unlimited depth.** The budget is fixed when the keys are generated.
- **Cheap operations.** Each multiplication touches every tower of every polynomial, which is why the server uses GPUs.
