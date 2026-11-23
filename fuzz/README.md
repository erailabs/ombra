# Fuzzing

libFuzzer targets for the parsers of untrusted bytes that build on the CPU (no CUDA, no gRPC). They are built with clang and `-fsanitize=fuzzer,address,undefined`, only when CMake gets `-DOMBRA_FUZZ=ON`.

| Target | Parser under test |
| --- | --- |
| `envelope` | file envelope reader, `core/include/ombra/core/file_envelope.hpp` |
| `seeded` | seeded ciphertext payload, `core/include/ombra/core/seeded_ciphertext.hpp` |
| `segment` | segment file, column envelopes and the insert-time column check, `server/storage/src/segment.cpp` |
| `blob` | blob file, `server/storage/src/blob.cpp` |
| `wal` | write-ahead log records and torn-tail repair, `server/storage/src/wal.cpp` |
| `recovery` | crash recovery replay of a fuzzed `wal.log` over a small data dir, `Store::recover` |
| `api_keys` | API key file, `ApiKeyStore::load` and `reload` |
| `http` | REST request parser and the `Authorization` check on its output |
| `auth_header` | `authorize()` on raw header values, and the flag parsers of `limits.cpp` |
| `record_column` | record column parser (`ORCL`) and record file container (`OREC`), `server/storage/src/record_column.cpp` |
| `retrieve` | `RetrieveRecords` request stream validation, `server/grpc/src/retrieve.cpp`. Input: cluster count byte, then u16-length-prefixed messages. Needs gRPC (built when `ombra_server_retrieve` exists) |
| `proto` | `ParseFromArray` of every gRPC request message (first byte picks the type), a serialize and parse round trip, and `validate_spec` on a parsed `CreateCollectionRequest`. Needs gRPC (built when `ombra_proto` exists) |
| `keys` | `prepare_collection_keys` (`server/search/src/key_prep.cpp`): the preflight and then OpenFHE on a fuzzed context, EvalMult key or automorphism key file. Needs OpenFHE and the CPU engine |

`keys` modes (first byte): bits 0-1 pick the file the input replaces (0 context, 1 EvalMult, 2 automorphism, 3 none, a control that must be accepted). Bit 2 overwrites the start of a valid key file, bit 3 overwrites it from byte 840 on (structure: every change must be refused), bit 4 overwrites it from the first coefficient array on (the preflight accepts every such change, so OpenFHE parses damaged coefficients). With none of these bits set the input is the whole payload (the size check). Real key files are 1.5 MB or more, so the target makes a key set at start-up with a throw-away key (synthetic, in memory), and the corpus holds only 1-2 KiB slices of it (`OMBRA_FUZZ_DUMP_SEEDS=<dir>` writes them).

**Regression input.** `fuzz/known/keys-oom-evalmult-body.bin` (35 bytes) once made OpenFHE (cereal) allocate more than 2 GB: an EvalMult key file with a valid size and header but damaged bytes after the first 840, where cereal reads a length prefix. The preflight now checks every structure byte of a key file against a template (`server/search/context_golden/`), so the input is refused before OpenFHE reads it. It is also a test in the normal C++ suite (`search_context_preflight`).

Not covered here: the inline checks of the gRPC handlers in `server/grpc/src/service.cpp` (id length, id count, chunk size), which need a live server. The SQLite catalog is SQLite's own file format and is not fuzzed.

`record_column` and `retrieve` also run over their committed corpus in the normal test run (`make test`, ctest names `storage_record_column_corpus` and `server_retrieve_corpus`), with `fuzz/corpus_driver.cpp` instead of libFuzzer. The `retrieve` corpus is written by hand (like `proto`), not by `make fuzz-corpus`.

## Run

```
make fuzz FUZZ_SECONDS=60
```

This builds `build-fuzz` and runs every target for `FUZZ_SECONDS` on a copy of its corpus. It fails if a target crashes, leaks, times out or trips a sanitizer. The input that did it is left in `build-fuzz/fuzz-artifacts/`. To run one target for longer:

```
make fuzz FUZZ_SECONDS=600  # all targets
fuzz/run_fuzzers.sh build-fuzz 600 wal
```

Reproduce a finding with `build-fuzz/fuzz/ombra_fuzz_<target> <artifact file>`. Minimise it with `-minimize_crash=1`. Every finding gets a regression test in the normal C++ test suite, so `make test` catches it without fuzzing.

## How the targets are built

- The first byte of the `segment`, `blob`, `wal` and `recovery` inputs is a mode byte. With bit 0 set the harness repairs magic, version and checksums before parsing, so the fuzzer can reach the code behind the checksum. The `blob` target, bit 1, wraps the input in a valid blob of the smallest bucket size.
- A target that writes files uses one private directory under the temp dir and removes it at exit and on a crash. Set `TMPDIR` to choose where. `make fuzz` uses `/dev/shm` when it exists.
- A parser that accepts an input must produce the same bytes when it writes the parsed value back (envelope, seeded payload, segment, blob). The harness aborts if not.

## Corpus and dictionaries

`fuzz/corpus/<target>/` holds a few small valid inputs per target, built from the project's own writers with synthetic data only. `make fuzz-corpus` regenerates them. It first deletes the whole `fuzz/corpus` directory, then writes the corpora that the seed generator knows. The hand-written `retrieve` and `proto` corpora are not among them, so they are gone afterwards: restore them with `git checkout -- fuzz/corpus/retrieve fuzz/corpus/proto`. `fuzz/dict/<target>.dict` lists magic strings and format versions.
