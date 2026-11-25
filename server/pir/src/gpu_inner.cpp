// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
#include "ombra/pir/gpu_inner.hpp"

#include <memory>
#include <stdexcept>

#include "ombra/core/pir_inner.hpp"
#include "ombra/core/pir_inner_gpu.hpp"

namespace ombra::pir {
namespace {

using lbcrypto::Ciphertext;
using lbcrypto::DCRTPoly;

struct State {
  std::unique_ptr<core::EncodeTables> tables;
  std::unique_ptr<core::PirInnerDevice> device;
  std::size_t r1 = 0, r2 = 0;
};

}  // namespace

InnerSums make_gpu_inner_sums() {
  auto st = std::make_shared<State>();
  return [st](const PirContext& cc, const std::vector<Ciphertext<DCRTPoly>>& selectors,
              const std::vector<std::int64_t>& cells, std::size_t r1,
              std::size_t r2) -> std::vector<Ciphertext<DCRTPoly>> {
    if (selectors.size() != r1 || r1 == 0 || r2 == 0) {
      throw std::invalid_argument("gpu inner sums: bad selector count or grid");
    }
    // Tables once per call: they depend on the context only (extract_pir_inner_tables checks the
    // 128-bit floor and that the scheme is BFV).
    if (!st->tables) {
      st->tables = std::make_unique<core::EncodeTables>(
          core::extract_pir_inner_tables(cc, selectors.front()));
    }
    if (!st->device || st->r1 != r1 || st->r2 != r2) {
      st->device.reset();
      st->device = std::make_unique<core::PirInnerDevice>(*st->tables, r1, r2);
      st->r1 = r1;
      st->r2 = r2;
    }
    if (cells.size() != r1 * r2 * st->tables->ring_dim) {
      throw std::invalid_argument("gpu inner sums: wrong cell count");
    }
    const std::vector<std::uint64_t> words = core::flatten_pir_ciphertexts(selectors, *st->tables);
    std::vector<std::uint64_t> out;
    st->device->inner(words.data(), cells.data(), out);
    const std::size_t ct_words = 2 * st->device->poly_words();
    std::vector<Ciphertext<DCRTPoly>> inner;
    inner.reserve(r2);
    for (std::size_t b = 0; b < r2; ++b) {
      inner.push_back(
          core::pir_ciphertext_from_words(selectors.front(), out.data() + b * ct_words, ct_words));
    }
    return inner;
  };
}

void register_gpu_inner_sums() {
  set_gpu_inner_sums_factory([] { return make_gpu_inner_sums(); });
}

}  // namespace ombra::pir
