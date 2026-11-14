// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Private to server/search. Preflight of the raw bytes of an uploaded crypto context and of the
// evaluation key files, run BEFORE OpenFHE (cereal) reads them: a hostile payload must not reach
// the deserializer.
//
// Context: OpenFHE writes a crypto context deterministically from its parameters (checked against
// the real Python client for every preset: same bytes in every process, before and after key
// generation; see server/search/tests/context_preflight_test.cpp). So the server builds each
// preset's context itself, from the generated table of core/params/ckks_presets.json and with the
// same settings as client/ombra/params.py, serializes it once, and accepts an upload only if its
// bytes are identical. A context that passes was written by OpenFHE from the preset's own
// parameters, so it is at least 128-bit secure whatever the upload claims.
//
// Two writers, one set of parameters: the C++ writer (this server) and the one in openfhe-python
// (the client) differ in seven bytes for both presets, which are class-version words (1 in C++, 0
// in the Python module; both load). So two byte strings are accepted per preset: the server's own,
// and the client's, embedded from context_golden/ (a test rebuilds it with the real client and
// checks that it differs from the server's only where the C++ side has 1 and the client 0).
//
// Evaluation keys: their coefficients depend on a secret key, so no byte comparison is possible,
// but nothing else does. A key file is a few bytes of cereal structure (length prefixes, counts,
// version words, polymorphic names, moduli, ring dimension, the key tag, map size, rotation
// indices) around polynomial value arrays of exactly N 8-byte words. context_golden/generate.py
// learns, from independent key generations of the real client, a template of the structure; the
// server requires every structure byte of a key file to equal it and lets only the coefficient
// words differ. cereal allocates from a length prefix, so this keeps a damaged prefix away from
// OpenFHE (a payload with a valid header and size used to make it allocate over 2 GB; fuzz/README.md).
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace ombra::search::detail {

// One row of the generated table (the fields of core/params/ckks_presets.json).
struct PresetParams {
  const char* name;
  const char* security_level;  // OpenFHE name, e.g. "HEStd_128_classic"
  std::size_t ring_dim;
  std::size_t depth;
  std::size_t scale_bits;
  std::size_t first_bits;
  std::size_t batch;
  const char* scaling_technique;
  const char* key_switch_technique;
  std::uint32_t num_large_digits;  // 0: not set, OpenFHE chooses
  // The context as the real Python client serializes it (context_golden/<name>.bin).
  const unsigned char* client_context;
  std::size_t client_context_size;
  // Structure templates of the key files (context_golden/<name>.{evalmult,auto}_keys).
  const unsigned char* evalmult_template;
  std::size_t evalmult_template_size;
  const unsigned char* auto_template;
  std::size_t auto_template_size;
};

// Builds a context from `p` the way the client does and returns its BINARY serialization.
// Throws std::runtime_error on an unknown technique name.
std::vector<std::uint8_t> build_context_bytes(const PresetParams& p);

// Table row of a preset, or nullptr.
const PresetParams* find_preset_params(const std::string& preset);

// The serialized context of the preset (built once, on first use), or nullptr for an unknown
// preset.
const std::vector<std::uint8_t>* canonical_context(const std::string& preset);

// The same context as the Python client writes it (from context_golden/), or nullptr.
const std::vector<std::uint8_t>* canonical_client_context(const std::string& preset);

// Throws InvalidQuery unless `payload` is byte-identical to the preset's context (either writer).
void require_canonical_context(const std::string& preset, const std::vector<std::uint8_t>& payload);

enum class KeyKind { EvalMult, Automorphism };

// Throws InvalidQuery unless the key file matches the structure template of the preset byte for
// byte, except the coefficient words of the polynomial value arrays:
//  - EvalMult: one key.
//  - Automorphism: n keys (1 to log2(N)) with the rotation indices N/2^(n-1-j)+1 in ascending
//    order, which is what the client sends for a collection (client/ombra/query.py), the map size
//    n, and the pointer ids cereal gives. Its size, which the template fixes for each n, tells
//    which n is expected.
// The key tag (32 lowercase hex characters) must be the same everywhere in the file. The message
// names the first offset that does not match. Reads only from `payload`; allocates nothing from it.
void require_key_file(const std::string& preset, KeyKind kind,
                      const std::vector<std::uint8_t>& payload);

// Test support: where the free (coefficient) words of a key file of `n_keys` keys are, and where
// the key tag is written, according to the preset's template. n_keys is 1 for EvalMult.
struct KeyFileMap {
  std::size_t size = 0;
  std::vector<std::pair<std::size_t, std::size_t>> free_ranges;  // [begin, end) byte offsets
  std::vector<std::size_t> tag_offsets;
  std::size_t max_keys = 0;
};
KeyFileMap describe_key_file(const std::string& preset, KeyKind kind, std::size_t n_keys);

// Test support: overwrite every structure byte of `file` (not the tag, not the free words) with
// what the template requires. The file must already have the size of a valid key file. Used to
// turn a key file written by the C++ side of this process (which writes 7 class-version words
// differently from the Python client) into the bytes the client writes.
void conform_key_file_to_template(const std::string& preset, KeyKind kind,
                                  std::vector<std::uint8_t>& file);

}  // namespace ombra::search::detail
