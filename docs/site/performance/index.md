# Performance

Performance figures are TBD. They are being re-measured on the current code and are not published yet. This page lists only sizes that follow from the ciphertext format, and the commands to measure on your own hardware. To pick a preset, see [Choosing a preset](../security/parameters.md#choosing-a-preset).

## Sizes

Each dimension of each vector takes 48 bytes on disk, against 4 bytes for a plain float32: 12 times the size. A ciphertext holds one dimension of many vectors, one per slot ({{ preset("balanced", "batch_size") }} for Balanced, {{ preset("fast", "batch_size") }} for Fast). Both presets end up at the same size per vector, because halving the ring halves the slots.

A ciphertext is a pair of polynomials, and one is uniformly random. The client stores a 32-byte seed in its place and the server regenerates it on load (a seeded ciphertext, which needs the compiled client module in the wheels). A stored Balanced ciphertext is 393,336 bytes for 8,192 slots (48.01 bytes per dimension per vector); unseeded it is 787,423 bytes (96.12). The GPU holds the same data either way.

Per million vectors, Balanced, whole blocks of 8,192 vectors included:

| Dimension | Encrypted (seeded) | Encrypted (not seeded) | Plain float32 |
| --- | --- | --- | --- |
| 384 | 18.6 GB | 37.2 GB | 1.5 GB |
| 768 | 37.2 GB | 74.4 GB | 3.1 GB |
| 1536 | 74.3 GB | 148.8 GB | 6.1 GB |

A real collection takes more: keys, sealed record blobs, cluster centers, partly filled blocks, and one column per filter field value (two fields of 8 values add 16 columns). Measure your own before planning disk. There is no plaintext mode.

Public evaluation keys for Balanced, uploaded once per collection (Fast keys are about 40% smaller):

| Dimension | Rotation keys | Relinearization key | Total |
| --- | --- | --- | --- |
| 384 | 26.2 MB | 2.6 MB | 28.8 MB |
| 768 | 28.8 MB | 2.6 MB | 31.5 MB |
| 1536 | 31.5 MB | 2.6 MB | 34.1 MB |

A query upload is 787,423 bytes for Balanced and 394,207 for Fast.

Vectors that fit in one A100 (40 GB) for Balanced, with 1 GiB left for scratch (calculated, not measured): 1,105,920 at 384, 548,864 at 768 and 270,336 at 1536. Larger collections work; the server pages the rest from disk and host memory, which is slower.

## Reproducing

Timings, throughput and recall come from scripts in the repository. GPU runs need a Modal account: set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in your environment, never in a file.

```bash
make bench TASK=<name>                      # A100 on Modal; tasks are in core/bench/run.py
make results-pull                           # copy results from the Modal volume
python tests/bench_local_mode.py --d 384    # local mode, CPU
ombra-recall                                # plaintext vs encrypted recall on your own data
```

Sizes need no GPU: `tests/test_openfhe_sizes.py` prints the serialized size of every object for a preset and dimension. Divide by the slots for the size per vector; a seeded stored column is half the printed size. Compare ratios before single milliseconds, and keep each run's raw JSON with the command, commit, GPU and library versions.
