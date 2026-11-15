# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
"""Turn range-filters JSON (from range_filters.py) into markdown tables.

    python core/bench/range_filters_report.py bench-cache/out/range-fast.json
"""

import json
import sys

d = json.load(open(sys.argv[1]))
print(f"Preset {d['preset']}, {d['queries']} queries per cell, {d['n']} vectors.\n")
for dist in sorted({c["dist"] for c in d["cells"]}):
    print(f"### {dist}\n")
    print(
        "| share | actual | method | recall@10 | fewer than k | edge FP (data) | "
        "edge FP (fetched) | candidates | fetched | latency ms (median) |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|")
    for c in d["cells"]:
        if c["dist"] != dist:
            continue
        print(
            f"| {c['share']:.0%} | {c['actual_share']:.1%} | {c['method']} | {c['recall']:.3f} | "
            f"{c['incomplete']:.0%} | {c['edge_fp_rate_data']:.0%} | "
            f"{c['edge_fp_rate_fetched']:.0%} | {c['candidates_mean']:.0f} | {c['fetched']:.0f} | "
            f"{c['latency_ms']:.0f} |"
        )
    print()
