// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// The layout numbers of the retrieval preset, from the generated header (no OpenFHE needed, so the
// router can size a call with them).
#include "ombra/pir/preset.hpp"
#include "ombra/pir/retrieval.hpp"

namespace ombra::pir {

PirParams retrieval_params() { return PirParams{preset::kRingDim, preset::kPlaintextModulus}; }

}  // namespace ombra::pir
