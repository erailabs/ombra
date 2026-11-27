// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Host side of the GPU second-dimension product (see ombra/core/pir_product.hpp). Kernels:
// core/kernels/bfv_product.cu (basis extension, tensor product, scale and round), ntt.cu (NTT).
// Internal code returns Status; the public functions convert to exceptions at the boundary.

#include <algorithm>
#include <stdexcept>

#include "device_buffer.cuh"
#include "kernel_launch.hpp"
#include "ombra/core/cuda_check.hpp"
#include "ombra/core/pir_product.hpp"

namespace ombra::core {
namespace {

constexpr std::size_t kNttChunk = 1024;  // copies per NTT launch (same bound as pir_inner_host.cu)

template <typename T>
Status to_device(DeviceBuffer<T>& buf, const std::vector<T>& host) {
  OMBRA_TRY(buf.allocate(host.size()));
  OMBRA_CUDA_CHECK(
      cudaMemcpy(buf.get(), host.data(), host.size() * sizeof(T), cudaMemcpyHostToDevice));
  return {};
}

}  // namespace

struct PirProductDevice::Impl {
  PirProductTables t;
  std::size_t pairs = 0, n = 0, nq = 0, nr = 0, nqr = 0;
  DeviceBuffer<TowerConst> tc;
  DeviceBuffer<u64> psi, psi_shoup, psi_inv, psi_inv_shoup;
  DeviceBuffer<u64> ex_hat_inv, ex_hat_mod, ex_alpha, b_hat_inv, b_conv, r_hat_inv, r_hat_mod,
      r_alpha, sr_tab;
  DeviceBuffer<double> q_inv, r_inv, sr_frac;
  DeviceBuffer<NttRow> rows_q_inv, rows_r_fwd, rows_qr_fwd, rows_t_inv;
  DeviceBuffer<u64> in_x, in_y, coef, ext_x, ext_y, tens, out;
  BfvProdTables d;
  cudaEvent_t e0 = nullptr, e1 = nullptr;
  double seconds = 0;

  ~Impl() {
    // Destructors cannot return a Status; failures are ignored on purpose (nothing to recover).
    for (cudaEvent_t e : {e0, e1}) {
      if (e != nullptr) (void)cudaEventDestroy(e);
    }
  }

  Status init() {
    OMBRA_TRY(to_device(tc, make_tower_consts(t.moduli)));
    OMBRA_TRY(to_device(psi, t.psi_rev));
    OMBRA_TRY(to_device(psi_inv, t.psi_inv_rev));
    std::vector<u64> s1(t.psi_rev.size()), s2(t.psi_rev.size());
    for (std::size_t x = 0; x < nqr; ++x) {
      for (std::size_t i = 0; i < n; ++i) {
        s1[x * n + i] = shoup_quotient(t.psi_rev[x * n + i], t.moduli[x]);
        s2[x * n + i] = shoup_quotient(t.psi_inv_rev[x * n + i], t.moduli[x]);
      }
    }
    OMBRA_TRY(to_device(psi_shoup, s1));
    OMBRA_TRY(to_device(psi_inv_shoup, s2));
    OMBRA_TRY(to_device(ex_hat_inv, t.ex_hat_inv));
    OMBRA_TRY(to_device(ex_hat_mod, t.ex_hat_mod));
    OMBRA_TRY(to_device(ex_alpha, t.ex_alpha));
    OMBRA_TRY(to_device(q_inv, t.q_inv));
    OMBRA_TRY(to_device(b_hat_inv, t.b_hat_inv));
    OMBRA_TRY(to_device(b_conv, t.b_conv));
    OMBRA_TRY(to_device(r_hat_inv, t.r_hat_inv));
    OMBRA_TRY(to_device(r_hat_mod, t.r_hat_mod));
    OMBRA_TRY(to_device(r_alpha, t.r_alpha));
    OMBRA_TRY(to_device(r_inv, t.r_inv));
    OMBRA_TRY(to_device(sr_tab, t.sr_tab));
    OMBRA_TRY(to_device(sr_frac, t.sr_frac));
    d.n = n;
    d.nq = (u32)nq;
    d.nr = (u32)nr;
    d.tc = tc.get();
    d.ex_hat_inv = ex_hat_inv.get();
    d.ex_hat_mod = ex_hat_mod.get();
    d.ex_alpha = ex_alpha.get();
    d.q_inv = q_inv.get();
    d.b_hat_inv = b_hat_inv.get();
    d.b_conv = b_conv.get();
    d.r_hat_inv = r_hat_inv.get();
    d.r_hat_mod = r_hat_mod.get();
    d.r_alpha = r_alpha.get();
    d.r_inv = r_inv.get();
    d.sr_tab = sr_tab.get();
    d.sr_frac = sr_frac.get();

    std::vector<NttRow> a, b, c, e;
    for (std::size_t i = 0; i < nq; ++i) {
      a.push_back({i * n, t.n_inv[i], shoup_quotient(t.n_inv[i], t.moduli[i]), (u32)i, 0});
    }
    for (std::size_t j = 0; j < nr; ++j) b.push_back({(nq + j) * n, 0, 0, (u32)(nq + j), 0});
    for (std::size_t i = 0; i < nqr; ++i) c.push_back({i * n, 0, 0, (u32)i, 0});
    for (std::size_t comp = 0; comp < 3; ++comp) {
      for (std::size_t i = 0; i < nqr; ++i) {
        e.push_back(
            {(comp * nqr + i) * n, t.n_inv[i], shoup_quotient(t.n_inv[i], t.moduli[i]), (u32)i, 0});
      }
    }
    OMBRA_TRY(to_device(rows_q_inv, a));
    OMBRA_TRY(to_device(rows_r_fwd, b));
    OMBRA_TRY(to_device(rows_qr_fwd, c));
    OMBRA_TRY(to_device(rows_t_inv, e));
    const std::size_t polys = pairs * 2;
    OMBRA_TRY(in_x.allocate(polys * nq * n));
    OMBRA_TRY(in_y.allocate(polys * nq * n));
    OMBRA_TRY(coef.allocate(polys * nq * n));
    OMBRA_TRY(ext_x.allocate(polys * nqr * n));
    OMBRA_TRY(ext_y.allocate(polys * nqr * n));
    OMBRA_TRY(tens.allocate(pairs * 3 * nqr * n));
    OMBRA_TRY(out.allocate(3 * nq * n));
    OMBRA_CUDA_CHECK(cudaEventCreate(&e0));
    OMBRA_CUDA_CHECK(cudaEventCreate(&e1));
    return {};
  }

  NttTables ntt() const {
    return {psi.get(), psi_shoup.get(), psi_inv.get(), psi_inv_shoup.get(), tc.get(), n};
  }

  // NTT over `count` polynomials of `row_count` rows each, `stride` words apart, in chunks.
  Status ntt_chunks(bool forward, u64* data, const NttRow* rows, u32 row_count, std::size_t count,
                    std::size_t stride) {
    for (std::size_t c0 = 0; c0 < count; c0 += kNttChunk) {
      NttBatch batch;
      batch.count = (u32)std::min(kNttChunk, count - c0);
      batch.stride = stride;
      u64* p = data + c0 * stride;
      if (forward) {
        OMBRA_CUDA_CHECK(launch_ntt_forward(p, rows, row_count, ntt(), nullptr, batch));
      } else {
        OMBRA_CUDA_CHECK(launch_ntt_inverse(p, rows, row_count, ntt(), nullptr, batch));
      }
    }
    return {};
  }

  // Extends the polys in `in` (Q towers, evaluation form) into `ext` (Q R towers, evaluation form).
  Status extend(const u64* in, u64* ext, bool exact) {
    const std::size_t polys = pairs * 2;
    // The Q towers go to ext unchanged; their coefficient form is made in `coef`.
    OMBRA_CUDA_CHECK(cudaMemcpy2D(ext, nqr * n * sizeof(u64), in, nq * n * sizeof(u64),
                                  nq * n * sizeof(u64), polys, cudaMemcpyDeviceToDevice));
    OMBRA_CUDA_CHECK(
        cudaMemcpy(coef.get(), in, polys * nq * n * sizeof(u64), cudaMemcpyDeviceToDevice));
    OMBRA_TRY(ntt_chunks(false, coef.get(), rows_q_inv.get(), (u32)nq, polys, nq * n));
    if (exact) {
      OMBRA_CUDA_CHECK(launch_bfv_expand_exact(coef.get(), ext, d, polys, nullptr));
      OMBRA_TRY(ntt_chunks(true, ext, rows_r_fwd.get(), (u32)nr, polys, nqr * n));
    } else {
      OMBRA_CUDA_CHECK(launch_bfv_expand_scaled(coef.get(), ext, d, polys, nullptr));
      OMBRA_TRY(ntt_chunks(true, ext, rows_qr_fwd.get(), (u32)nqr, polys, nqr * n));
    }
    return {};
  }

  Status run(const u64* x, const u64* y, std::vector<u64>& out_h) {
    const std::size_t words = pairs * 2 * nq * n;
    OMBRA_CUDA_CHECK(cudaMemcpy(in_x.get(), x, words * sizeof(u64), cudaMemcpyHostToDevice));
    OMBRA_CUDA_CHECK(cudaMemcpy(in_y.get(), y, words * sizeof(u64), cudaMemcpyHostToDevice));
    OMBRA_CUDA_CHECK(cudaEventRecord(e0, nullptr));
    OMBRA_TRY(extend(in_x.get(), ext_x.get(), true));
    OMBRA_TRY(extend(in_y.get(), ext_y.get(), false));
    OMBRA_CUDA_CHECK(launch_bfv_tensor(ext_x.get(), ext_y.get(), tens.get(), d, pairs, nullptr));
    OMBRA_TRY(ntt_chunks(false, tens.get(), rows_t_inv.get(), (u32)(3 * nqr), pairs, 3 * nqr * n));
    OMBRA_CUDA_CHECK(launch_bfv_scale_round_sum(tens.get(), out.get(), d, pairs, nullptr));
    OMBRA_CUDA_CHECK(cudaEventRecord(e1, nullptr));
    OMBRA_CUDA_CHECK(cudaEventSynchronize(e1));
    OMBRA_CUDA_CHECK_LAUNCH();
    float ms = 0;
    OMBRA_CUDA_CHECK(cudaEventElapsedTime(&ms, e0, e1));
    seconds = ms / 1000.0;
    out_h.assign(3 * nq * n, 0);
    OMBRA_CUDA_CHECK(
        cudaMemcpy(out_h.data(), out.get(), out_h.size() * sizeof(u64), cudaMemcpyDeviceToHost));
    return {};
  }
};

PirProductDevice::PirProductDevice(const PirProductTables& t, std::size_t pairs)
    : impl_(std::make_unique<Impl>()) {
  const std::size_t n = t.ring_dim, nqr = t.nq + t.nr;
  if (n < 4 || (n & (n - 1)) != 0 || t.nq == 0 || t.nr == 0 || t.nq > kMaxTowers ||
      t.nr > kMaxTowers || t.moduli.size() != nqr || t.psi_rev.size() != nqr * n ||
      t.psi_inv_rev.size() != nqr * n || t.n_inv.size() != nqr || t.ex_hat_inv.size() != t.nq ||
      t.ex_hat_mod.size() != t.nr * t.nq || t.ex_alpha.size() != (t.nq + 1) * t.nr ||
      t.q_inv.size() != t.nq || t.b_hat_inv.size() != t.nq || t.b_conv.size() != t.nq * t.nr ||
      t.r_hat_inv.size() != t.nr || t.r_hat_mod.size() != t.nq * t.nr ||
      t.r_alpha.size() != (t.nr + 1) * t.nq || t.r_inv.size() != t.nr ||
      t.sr_tab.size() != t.nq * (t.nr + 1) || t.sr_frac.size() != t.nr) {
    throw std::invalid_argument("pir product: bad tables");
  }
  // One grid row per polynomial in the expansion kernels.
  if (pairs == 0 || pairs * 2 > 65535) throw std::invalid_argument("pir product: bad pair count");
  for (const std::uint64_t q : t.moduli) {
    if (q < 3 || (q & 1) == 0 || q >= (std::uint64_t{1} << 60)) {
      throw std::invalid_argument("pir product: moduli must be odd and below 2^60");
    }
  }
  impl_->t = t;
  impl_->pairs = pairs;
  impl_->n = n;
  impl_->nq = t.nq;
  impl_->nr = t.nr;
  impl_->nqr = nqr;
  impl_->init().throw_if_error();
}

PirProductDevice::~PirProductDevice() = default;

std::size_t PirProductDevice::pairs() const { return impl_->pairs; }
std::size_t PirProductDevice::ct_words() const { return 2 * impl_->nq * impl_->n; }
std::size_t PirProductDevice::out_words() const { return 3 * impl_->nq * impl_->n; }
double PirProductDevice::last_gpu_seconds() const { return impl_->seconds; }

void PirProductDevice::product_sum(const std::uint64_t* x, const std::uint64_t* y,
                                   std::vector<std::uint64_t>& out) {
  if (x == nullptr || y == nullptr) throw std::invalid_argument("pir product: null input");
  const std::size_t chunks = impl_->pairs * 2 * impl_->nq;
  for (const std::uint64_t* in : {x, y}) {
    for (std::size_t c = 0; c < chunks; ++c) {
      const std::uint64_t q = impl_->t.moduli[c % impl_->nq];
      const std::uint64_t* p = in + c * impl_->n;
      for (std::size_t i = 0; i < impl_->n; ++i) {
        if (p[i] >= q) throw std::invalid_argument("pir product: unreduced input word");
      }
    }
  }
  impl_->run(x, y, out).throw_if_error();
}

}  // namespace ombra::core
