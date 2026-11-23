// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Host side of the GPU inner products of the retrieval answer (see ombra/core/pir_inner.hpp).
// Kernels: core/kernels/encode.cu (reduction), ntt.cu (forward NTT), plain_search.cu (products).
// Internal code returns Status; the public functions convert to exceptions at the boundary.

#include <algorithm>
#include <cstring>
#include <stdexcept>

#include "device_buffer.cuh"
#include "kernel_launch.hpp"
#include "ombra/core/cuda_check.hpp"
#include "ombra/core/pir_inner.hpp"

namespace ombra::core {
namespace {

static_assert(sizeof(long long) == sizeof(std::int64_t), "int64 coefficients are copied as is");

// Cells transformed per NTT launch (same bound as the plaintext encoder).
constexpr std::size_t kNttChunk = 1024;

template <typename T>
Status to_device(DeviceBuffer<T>& buf, const std::vector<T>& host) {
  OMBRA_TRY(buf.allocate(host.size()));
  OMBRA_CUDA_CHECK(
      cudaMemcpy(buf.get(), host.data(), host.size() * sizeof(T), cudaMemcpyHostToDevice));
  return {};
}

}  // namespace

struct PirInnerDevice::Impl {
  EncodeTables t;
  std::size_t r1 = 0, r2 = 0, n = 0, nt = 0, poly = 0;
  DeviceBuffer<TowerConst> tc;
  DeviceBuffer<u64> psi, psi_shoup;
  DeviceBuffer<NttRow> rows;
  DeviceBuffer<long long> cells;  // [b][a][N]: the cell a * r2 + b of the grid sits at b * r1 + a
  DeviceBuffer<u64> plain;        // [b][a][tower][N], evaluation form
  DeviceBuffer<u64> sel, out;
  long long* stage = nullptr;  // pinned host copy of the cells in device order, made once
  cudaEvent_t e0 = nullptr, e1 = nullptr;
  double seconds = 0;

  ~Impl() {
    // Destructors cannot return a Status; failures are ignored on purpose (nothing to recover).
    for (cudaEvent_t e : {e0, e1}) {
      if (e != nullptr) (void)cudaEventDestroy(e);
    }
    if (stage != nullptr) (void)cudaFreeHost(stage);
  }

  Status init() {
    OMBRA_TRY(to_device(tc, make_tower_consts(t.moduli)));
    OMBRA_TRY(to_device(psi, t.psi_rev));
    std::vector<u64> shoup(t.psi_rev.size());
    for (std::size_t x = 0; x < nt; ++x) {
      for (std::size_t i = 0; i < n; ++i) {
        shoup[x * n + i] = shoup_quotient(t.psi_rev[x * n + i], t.moduli[x]);
      }
    }
    OMBRA_TRY(to_device(psi_shoup, shoup));
    std::vector<NttRow> r(nt);
    for (std::size_t x = 0; x < nt; ++x) r[x] = {x * n, 0, 0, static_cast<u32>(x), 0};
    OMBRA_TRY(to_device(rows, r));
    OMBRA_TRY(cells.allocate(r1 * r2 * n));
    OMBRA_TRY(plain.allocate(r1 * r2 * poly));
    OMBRA_TRY(sel.allocate(r1 * 2 * poly));
    OMBRA_TRY(out.allocate(r2 * 2 * poly));
    OMBRA_CUDA_CHECK(
        cudaMallocHost(reinterpret_cast<void**>(&stage), r1 * r2 * n * sizeof(long long)));
    OMBRA_CUDA_CHECK(cudaEventCreate(&e0));
    OMBRA_CUDA_CHECK(cudaEventCreate(&e1));
    return {};
  }

  // The forward transform reads only psi and tc; the inverse tables are never used here.
  NttTables ntt() const {
    return {psi.get(), psi_shoup.get(), psi.get(), psi_shoup.get(), tc.get(), n};
  }

  Status run(const u64* sel_h, const std::int64_t* cells_h, std::vector<u64>& out_h) {
    OMBRA_CUDA_CHECK(
        cudaMemcpy(sel.get(), sel_h, r1 * 2 * poly * sizeof(u64), cudaMemcpyHostToDevice));
    // Cell (a, b) goes to slot b * r1 + a, so that the products of one output b read r1
    // consecutive plaintexts (the layout of launch_plain_search). The reordering is done on the
    // host into pinned memory, then all cells cross to the device in one copy.
    for (std::size_t a = 0; a < r1; ++a) {
      for (std::size_t b = 0; b < r2; ++b) {
        std::memcpy(stage + (b * r1 + a) * n, cells_h + (a * r2 + b) * n, n * sizeof(long long));
      }
    }
    OMBRA_CUDA_CHECK(cudaMemcpy(cells.get(), stage, r1 * r2 * n * sizeof(long long),
                                cudaMemcpyHostToDevice));
    const std::size_t cols = r1 * r2;
    OMBRA_CUDA_CHECK(cudaEventRecord(e0, nullptr));
    OMBRA_CUDA_CHECK(launch_reduce_ints(cells.get(), cols, n, tc.get(), nt, plain.get(), nullptr));
    for (std::size_t c0 = 0; c0 < cols; c0 += kNttChunk) {
      NttBatch batch;
      batch.count = static_cast<u32>(std::min(kNttChunk, cols - c0));
      batch.stride = poly;
      OMBRA_CUDA_CHECK(launch_ntt_forward(plain.get() + c0 * poly, rows.get(), static_cast<u32>(nt),
                                          ntt(), nullptr, batch));
    }
    OMBRA_CUDA_CHECK(
        launch_plain_search(plain.get(), sel.get(), out.get(), tc.get(), r1, r2, nt, n, nullptr));
    OMBRA_CUDA_CHECK(cudaEventRecord(e1, nullptr));
    OMBRA_CUDA_CHECK(cudaEventSynchronize(e1));
    OMBRA_CUDA_CHECK_LAUNCH();
    float ms = 0;
    OMBRA_CUDA_CHECK(cudaEventElapsedTime(&ms, e0, e1));
    seconds = ms / 1000.0;
    out_h.assign(r2 * 2 * poly, 0);
    OMBRA_CUDA_CHECK(
        cudaMemcpy(out_h.data(), out.get(), out_h.size() * sizeof(u64), cudaMemcpyDeviceToHost));
    return {};
  }
};

PirInnerDevice::PirInnerDevice(const EncodeTables& tables, std::size_t r1, std::size_t r2)
    : impl_(std::make_unique<Impl>()) {
  const std::size_t n = tables.ring_dim;
  if (n < 4 || (n & (n - 1)) != 0 || tables.num_towers == 0 ||
      tables.moduli.size() != tables.num_towers || tables.psi_rev.size() != tables.num_towers * n) {
    throw std::invalid_argument("pir inner: bad tables");
  }
  // launch_plain_search: grid.y = ceil(r2 / 4) must fit in 65535.
  if (r1 == 0 || r2 == 0 || (r2 + 3) / 4 > 65535) {
    throw std::invalid_argument("pir inner: bad grid");
  }
  for (const std::uint64_t q : tables.moduli) {
    // the product kernel multiplies with Montgomery reduction (needs odd q; RNS primes are odd)
    if ((q & 1) == 0) throw std::invalid_argument("pir inner: moduli must be odd");
  }
  impl_->t = tables;
  impl_->r1 = r1;
  impl_->r2 = r2;
  impl_->n = n;
  impl_->nt = tables.num_towers;
  impl_->poly = tables.num_towers * n;
  impl_->init().throw_if_error();
}

PirInnerDevice::~PirInnerDevice() = default;

std::size_t PirInnerDevice::poly_words() const { return impl_->poly; }
std::size_t PirInnerDevice::selector_words() const { return impl_->r1 * 2 * impl_->poly; }
std::size_t PirInnerDevice::out_words() const { return impl_->r2 * 2 * impl_->poly; }
double PirInnerDevice::last_gpu_seconds() const { return impl_->seconds; }

void PirInnerDevice::inner(const std::uint64_t* selectors, const std::int64_t* cells,
                           std::vector<std::uint64_t>& out) {
  if (selectors == nullptr || cells == nullptr) {
    throw std::invalid_argument("pir inner: null input");
  }
  // The product kernel needs reduced words (it does not reduce them again).
  const std::size_t chunks = impl_->r1 * 2 * impl_->nt;
  for (std::size_t c = 0; c < chunks; ++c) {
    const std::uint64_t q = impl_->t.moduli[c % impl_->nt];
    const std::uint64_t* p = selectors + c * impl_->n;
    for (std::size_t i = 0; i < impl_->n; ++i) {
      if (p[i] >= q) throw std::invalid_argument("pir inner: unreduced selector word");
    }
  }
  impl_->run(selectors, cells, out).throw_if_error();
}

}  // namespace ombra::core
