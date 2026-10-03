#!/usr/bin/env python3
"""Run all standard scenarios, export CSV data, and verify reproducibility.

Usage:  python3 run_demo.py [output_dir]   (default: ./data)
"""

import os
import sys

from sendwindow.report import rows_to_csv, sha256_text, write_csv
from sendwindow.scenarios import standard_scenarios


def main(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    manifest_lines = []
    print("%-24s %-40s %s" % ("scenario", "cwnd series (MSS)", "events"))
    print("-" * 96)
    for name, scenario in standard_scenarios().items():
        first = scenario.run()
        second = scenario.run()
        first_csv = rows_to_csv(first.samples)
        second_csv = rows_to_csv(second.samples)
        assert first_csv == second_csv, "non-deterministic run: %s" % name
        assert first.cwnd_series == second.cwnd_series

        path = os.path.join(output_dir, "%s.csv" % name)
        write_csv(path, first.samples)
        digest = sha256_text(first_csv)
        manifest_lines.append("%s  %s" % (digest, os.path.basename(path)))

        events = [e for e in first.events if e != "ok"]
        print("%-24s %-40s %s" % (
            name,
            " ".join(str(c) for c in first.cwnd_series),
            ",".join(events) if events else "-",
        ))

    manifest_path = os.path.join(output_dir, "MANIFEST.sha256")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(manifest_lines) + "\n")
    print("-" * 96)
    print("wrote %d CSV files + %s" % (len(manifest_lines), manifest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data"))
