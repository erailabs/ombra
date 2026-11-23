// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The GPU backend of the retrieval answer's inner sums (CUDA build only): PirInnerDevice of core/
// behind the InnerSums hook of answer.hpp. Public material only (ciphertext words, cells).
#pragma once

#include "ombra/pir/answer.hpp"

namespace ombra::pir {

// A hook for one call. It builds the NTT tables from the first selectors it sees and the device
// when r1 and r2 are first seen, and reuses both for the later buckets of the call (a bucket
// with other r1 or r2 makes a new device). Not thread-safe; make one per call.
InnerSums make_gpu_inner_sums();

}  // namespace ombra::pir
