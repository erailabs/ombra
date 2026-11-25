# Presets

A preset fixes the encryption parameters. You pick a name and a dimension, never ring sizes or moduli. Every preset is at least 128-bit secure; the client refuses to build one that is not.

| Preset | Ring dimension $N$ | Slots | Multiplicative depth | Filter fields $F_{max}$ |
| --- | --- | --- | --- | --- |
| `fast` | {{ preset("fast", "ring_dim") }} | {{ preset("fast", "batch_size") }} | {{ preset("fast", "multiplicative_depth") }} | {{ fmax("fast") }} |
| `balanced` | {{ preset("balanced", "ring_dim") }} | {{ preset("balanced", "batch_size") }} | {{ preset("balanced", "multiplicative_depth") }} | {{ fmax("balanced") }} |

The filter penalty weight is $\lambda = {{ const("ombra.presets.FILTER_LAMBDA") }}$.

## Details

Bit sizes are those of the RNS moduli: the first holds the final result, and each scaling modulus matches the scale $\Delta = 2^{\text{scaling bits}}$.

| Preset | Core name | Security level | First modulus bits | Scaling modulus bits | Scaling technique | Key switching |
| --- | --- | --- | --- | --- | --- | --- |
| `fast` | `{{ const("ombra.presets.PRODUCT_PRESETS")["fast"] }}` | {{ preset("fast", "security_level") }} | {{ preset("fast", "first_mod_size") }} | {{ preset("fast", "scaling_mod_size") }} | {{ preset("fast", "scaling_technique") }} | {{ preset("fast", "key_switch_technique") }} |
| `balanced` | `{{ const("ombra.presets.PRODUCT_PRESETS")["balanced"] }}` | {{ preset("balanced", "security_level") }} | {{ preset("balanced", "first_mod_size") }} | {{ preset("balanced", "scaling_mod_size") }} | {{ preset("balanced", "scaling_technique") }} | {{ preset("balanced", "key_switch_technique") }} |

The core name is what a collection stores and the server checks against every ciphertext's envelope. `HEStd_128_classic` is OpenFHE's 128-bit level. See [Parameters](../security/parameters.md).

The default preset is `{{ const("ombra.presets.DEFAULT_PRESET") }}` against a server and `fast` in local mode. `precise` is reserved and refused for now.

## Dimensions

Supported dimensions: {{ const("ombra.presets.SUPPORTED_DIMENSIONS") | join(", ") }}. The dimension plus the sum of the filter fields' value counts must not exceed half the ring dimension (the query is one ciphertext). Slots are the vectors one ciphertext holds, so a block has at most that many.

## Filter limits

A collection can have at most {{ const("ombra.presets.MAX_FILTER_FIELDS") }} filter fields of at most {{ const("ombra.presets.MAX_FILTER_CARDINALITY") }} values each. The preset can lower the field limit, because filtering adds $\lambda$ times a count to every score, which must fit the lowest ciphertext level. $F_{max}$ is that limit:

$$F_{max} = \min\left(16,\ \left\lfloor \frac{2^{b-3} - 1}{\lambda} \right\rfloor - 1\right), \qquad b = \text{first bits} - \text{scaling bits} - 1$$

where 3 is a safety margin in bits. A tombstoned slot reaches $1 + \lambda (F + 1)$, which the bound protects.

{% set b = preset("balanced", "first_mod_size") - preset("balanced", "scaling_mod_size") - 1 %}
For `balanced`: $b = {{ preset("balanced", "first_mod_size") }} - {{ preset("balanced", "scaling_mod_size") }} - 1 = {{ b }}$, so $2^{b-3} = {{ 2 ** (b - 3) }}$, and $F_{max} = {{ fmax("balanced") }}$.

Sources: `client/ombra/resources/ckks_presets.json` (parameters; the server reads the same file at build time) and `client/ombra/presets.py` (layout and $F_{max}$).
