// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Kernel family: the BFV ciphertext x ciphertext product without relinearization, as OpenFHE v1.6.1
// computes it for the HPSPOVERQLEVELED technique at the top level (LeveledSHEBFVRNS::EvalMult,
// bfvrns-leveledshe.cpp). With Q = q_0..q_{nq-1} and R = r_0..r_{nr-1} (OpenFHE's P_l), for the
// operands x (the second-dimension selector) and y (the inner sum), both 2 components in
// evaluation form over Q:
//   1. x is extended exactly to Q R (DCRTPoly::ExpandCRTBasis: SwitchCRTBasis Q -> R with the
//      floating-point overflow count nu), the R towers are transformed to evaluation form;
//   2. y is scaled by R/Q and extended to Q R (FastExpandCRTBasisPloverQ: ApproxSwitchCRTBasis
//      Q -> R, then SwitchCRTBasis R -> Q), all towers transformed to evaluation form;
//   3. tensor product in evaluation form over Q R: [x0 y0, x0 y1 + x1 y0, x1 y1];
//   4. inverse transform, then ScaleAndRound by t/R back to Q (DCRTPoly::ScaleAndRound, output
//      basis Q: the R towers feed the floating-point count alpha), result in coefficient form.
// All integer steps are exact modular arithmetic. The two floating-point counts (nu, alpha) are
// computed with the same double operations in the same order as OpenFHE (separate multiply and
// add, no fused multiply-add: __dmul_rn / __dadd_rn; the installed OpenFHE has no FMA code), so
// every output word equals OpenFHE's. Tables come from the host (OpenFHE's crypto parameters);
// nothing is hard-coded here. Layouts (device, 64-bit words), "poly" = one ciphertext component:
//   coef/ext polys: [poly][tower][coeff], tower < nq (Q) or < nq + nr (Q R).

#include "kernel_launch.hpp"
#include "modarith.cuh"

namespace ombra::core {
namespace {

constexpr u32 kThreads = 256;

__device__ inline u64 mod128(u128 v, u64 q) { return (u64)(v % q); }

// Step 1, the R towers of x. Launch shape: grid = (ceil(N / kThreads), polys), block = kThreads.
// in: [poly][nq][N] coefficient form; out: [poly][nq + nr][N], only the R towers are written.
__global__ void expand_exact_kernel(const u64* __restrict__ in, u64* __restrict__ out,
                                    BfvProdTables t, u64 n) {
  const u64 k = (u64)blockIdx.x * blockDim.x + threadIdx.x;
  if (k >= n) return;
  const u64 poly = blockIdx.y;
  const u64 nqr = t.nq + t.nr;
  u64 xh[kMaxTowers];
  double nu = 0.5;
  for (u32 i = 0; i < t.nq; ++i) {
    const u64 x = in[(poly * t.nq + i) * n + k];
    xh[i] = mul_mod(x, t.ex_hat_inv[i], t.tc[i]);
    nu = __dadd_rn(nu, __dmul_rn((double)xh[i], t.q_inv[i]));
  }
  const u64 alpha = (u64)nu;
  for (u32 j = 0; j < t.nr; ++j) {
    const u64 p = t.tc[t.nq + j].q;
    u128 acc = 0;
    for (u32 i = 0; i < t.nq; ++i) acc += (u128)xh[i] * t.ex_hat_mod[j * t.nq + i];
    out[(poly * nqr + t.nq + j) * n + k] = sub_mod(mod128(acc, p), t.ex_alpha[alpha * t.nr + j], p);
  }
}

// Step 2, all towers of y. Launch shape as expand_exact_kernel. in: [poly][nq][N] coefficient form;
// out: [poly][nq + nr][N] coefficient form, Q towers then R towers.
__global__ void expand_scaled_kernel(const u64* __restrict__ in, u64* __restrict__ out,
                                     BfvProdTables t, u64 n) {
  const u64 k = (u64)blockIdx.x * blockDim.x + threadIdx.x;
  if (k >= n) return;
  const u64 poly = blockIdx.y;
  const u64 nqr = t.nq + t.nr;
  u64 xh[kMaxTowers], r[kMaxTowers];
  for (u32 i = 0; i < t.nq; ++i)
    xh[i] = mul_mod(in[(poly * t.nq + i) * n + k], t.b_hat_inv[i], t.tc[i]);
  for (u32 j = 0; j < t.nr; ++j) {
    const u64 p = t.tc[t.nq + j].q;
    u128 acc = 0;
    for (u32 i = 0; i < t.nq; ++i) acc += (u128)xh[i] * t.b_conv[i * t.nr + j];
    r[j] = mod128(acc, p);
    out[(poly * nqr + t.nq + j) * n + k] = r[j];
  }
  u64 y[kMaxTowers];
  double nu = 0.5;
  for (u32 j = 0; j < t.nr; ++j) {
    y[j] = mul_mod(r[j], t.r_hat_inv[j], t.tc[t.nq + j]);
    nu = __dadd_rn(nu, __dmul_rn((double)y[j], t.r_inv[j]));
  }
  const u64 alpha = (u64)nu;
  for (u32 j = 0; j < t.nq; ++j) {
    const u64 q = t.tc[j].q;
    u128 acc = 0;
    for (u32 i = 0; i < t.nr; ++i) acc += (u128)y[i] * t.r_hat_mod[j * t.nr + i];
    out[(poly * nqr + j) * n + k] = sub_mod(mod128(acc, q), t.r_alpha[alpha * t.nq + j], q);
  }
}

// Step 3. Launch shape: grid = (ceil(nqr * N / kThreads), pairs), block = kThreads. x, y:
// [pair][2][nqr][N] evaluation form; out: [pair][3][nqr][N].
__global__ void tensor_kernel(const u64* __restrict__ x, const u64* __restrict__ y,
                              u64* __restrict__ out, BfvProdTables t, u64 n) {
  const u64 nqr = t.nq + t.nr;
  const u64 e = (u64)blockIdx.x * blockDim.x + threadIdx.x;
  if (e >= nqr * n) return;
  const TowerConst c = t.tc[e / n];
  const u64 pw = nqr * n, pair = blockIdx.y;
  const u64* xp = x + pair * 2 * pw;
  const u64* yp = y + pair * 2 * pw;
  u64* op = out + pair * 3 * pw;
  const u64 x0 = xp[e], x1 = xp[pw + e], y0 = yp[e], y1 = yp[pw + e];
  op[e] = mul_mod(x0, y0, c);
  op[pw + e] = add_mod(mul_mod(x0, y1, c), mul_mod(x1, y0, c), c.q);
  op[2 * pw + e] = mul_mod(x1, y1, c);
}

// Step 4, summed over the pairs (the answer adds the products of the second dimension).
// Launch shape: grid = (ceil(N / kThreads), 3), block = kThreads. in: [pair][3][nqr][N]
// coefficient form; out: [3][nq][N] = sum over pairs of ScaleAndRound(in), coefficient form.
__global__ void scale_round_sum_kernel(const u64* __restrict__ in, u64* __restrict__ out,
                                       BfvProdTables t, u64 n, u64 pairs) {
  const u64 k = (u64)blockIdx.x * blockDim.x + threadIdx.x;
  if (k >= n) return;
  const u64 comp = blockIdx.y;
  const u64 nqr = t.nq + t.nr;
  u64 acc[kMaxTowers];
  for (u32 j = 0; j < t.nq; ++j) acc[j] = 0;
  for (u64 pr = 0; pr < pairs; ++pr) {
    const u64* base = in + (pr * 3 + comp) * nqr * n;
    double nu = 0.5;
    u64 xr[kMaxTowers];
    for (u32 i = 0; i < t.nr; ++i) {
      xr[i] = base[(t.nq + i) * n + k];
      nu = __dadd_rn(nu, __dmul_rn(t.sr_frac[i], (double)xr[i]));
    }
    const u64 alpha = (u64)nu;
    for (u32 j = 0; j < t.nq; ++j) {
      const u64 q = t.tc[j].q;
      const u64* tab = t.sr_tab + j * (t.nr + 1);
      u128 cur = 0;
      for (u32 i = 0; i < t.nr; ++i) cur += (u128)xr[i] * tab[i];
      cur += (u128)base[j * n + k] * tab[t.nr];
      const u64 v = add_mod(mod128(cur, q), alpha >= q ? alpha % q : alpha, q);
      acc[j] = add_mod(acc[j], v, q);
    }
  }
  for (u32 j = 0; j < t.nq; ++j) out[(comp * t.nq + j) * n + k] = acc[j];
}

}  // namespace

cudaError_t launch_bfv_expand_exact(const u64* in, u64* out, const BfvProdTables& t, u64 polys,
                                    cudaStream_t s) {
  const dim3 grid((unsigned)((t.n + kThreads - 1) / kThreads), (unsigned)polys);
  expand_exact_kernel<<<grid, kThreads, 0, s>>>(in, out, t, t.n);
  return cudaGetLastError();
}
cudaError_t launch_bfv_expand_scaled(const u64* in, u64* out, const BfvProdTables& t, u64 polys,
                                     cudaStream_t s) {
  const dim3 grid((unsigned)((t.n + kThreads - 1) / kThreads), (unsigned)polys);
  expand_scaled_kernel<<<grid, kThreads, 0, s>>>(in, out, t, t.n);
  return cudaGetLastError();
}
cudaError_t launch_bfv_tensor(const u64* x, const u64* y, u64* out, const BfvProdTables& t,
                              u64 pairs, cudaStream_t s) {
  const dim3 grid((unsigned)(((t.nq + t.nr) * t.n + kThreads - 1) / kThreads), (unsigned)pairs);
  tensor_kernel<<<grid, kThreads, 0, s>>>(x, y, out, t, t.n);
  return cudaGetLastError();
}
cudaError_t launch_bfv_scale_round_sum(const u64* in, u64* out, const BfvProdTables& t, u64 pairs,
                                       cudaStream_t s) {
  const dim3 grid((unsigned)((t.n + kThreads - 1) / kThreads), 3);
  scale_round_sum_kernel<<<grid, kThreads, 0, s>>>(in, out, t, t.n, pairs);
  return cudaGetLastError();
}

}  // namespace ombra::core
