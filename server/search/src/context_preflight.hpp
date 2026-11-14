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
// Evaluation keys: their bytes depend on a secret key, so no byte comparison is possible. The size
// is deterministic given the preset (and, for the automorphism keys, the number of keys), so the
// size is checked against what the preset's context gives. That bounds the input; it does not make
// cereal safe on a payload of the right size (fuzzed, see fuzz/README.md).
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
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

// Throws InvalidQuery unless `size` is a size an EvalMult key (one key) or an automorphism key
// file (1 to log2(N) keys) of the preset can have.
void require_key_size(const std::string& preset, KeyKind kind, std::size_t size);

}  // namespace ombra::search::detail
