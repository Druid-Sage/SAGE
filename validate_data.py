# Copyright 2026 Druid
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
validate_data.py — Validate that the lookup table CSVs are correctly formatted.

Run this whenever you update the data folder (e.g., after AH revision).
It checks:
  - All expected files exist (5 Appendix N + 80 Appendix O + EMC)
  - Filenames follow the naming convention
  - Headers match the expected columns
  - All cell values are numeric
  - Values are in plausible ranges
  - CN values across rows match expectations

Run as:
    python validate_data.py
"""

import csv
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

EXPECTED_ZONES = [1, 2, 3, 4, 5]
EXPECTED_RETENTION_DEPTHS = [0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00,
                              2.25, 2.50, 2.75, 3.00, 3.25, 3.50, 3.75, 4.00]
EXPECTED_CN_VALUES = [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98]

APPENDIX_N_HEADER = ["non_dcia_cn"] + [f"dcia_{d}" for d in range(0, 101, 5)]
APPENDIX_O_HEADER = ["non_dcia_cn"] + [f"dcia_{d}" for d in range(5, 101, 5)]
EMC_HEADER = ["land_use", "tn_mgL", "tp_mgL"]


def check_grid_csv(path, expected_header, value_min, value_max, label):
    """Returns (issues, warnings) lists."""
    issues, warnings = [], []
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    if not rows:
        issues.append(f"{label}: empty file")
        return issues, warnings
    header = rows[0]
    if header != expected_header:
        issues.append(
            f"{label}: header mismatch.\n    expected: {expected_header}\n    got:      {header}"
        )
        return issues, warnings
    cn_seen = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != len(expected_header):
            issues.append(f"{label}: row {i} has {len(row)} cols, expected {len(expected_header)}")
            continue
        try:
            cn = int(row[0])
            cn_seen.append(cn)
        except ValueError:
            issues.append(f"{label}: row {i} CN value not integer: {row[0]!r}")
            continue
        for j, cell in enumerate(row[1:], start=1):
            cell = cell.strip()
            if cell == "":
                continue
            try:
                v = float(cell)
            except ValueError:
                issues.append(f"{label}: row {i} col {j} not numeric: {cell!r}")
                continue
            if not (value_min <= v <= value_max):
                warnings.append(f"{label}: row {i} col {j} value {v} outside expected range [{value_min}, {value_max}]")
    if cn_seen != EXPECTED_CN_VALUES:
        warnings.append(f"{label}: CN sequence {cn_seen} != expected {EXPECTED_CN_VALUES}")
    return issues, warnings


def main():
    issues, warnings = [], []

    # Appendix N
    n_dir = DATA_DIR / "appendix_n_runoff_coefficients"
    for zone in EXPECTED_ZONES:
        f = n_dir / f"appendix_n_zone_{zone}.csv"
        if not f.exists():
            issues.append(f"Missing: {f}")
            continue
        i, w = check_grid_csv(f, APPENDIX_N_HEADER, 0.0, 1.0, f"appendix_n_zone_{zone}")
        issues.extend(i); warnings.extend(w)

    # Appendix O
    o_dir = DATA_DIR / "appendix_o_dry_retention"
    if not o_dir.exists():
        issues.append(f"Missing dir: {o_dir}")
    else:
        files = sorted(p for p in o_dir.iterdir() if p.suffix == ".csv")
        seen = set()
        rx = re.compile(r"^appendix_o_zone_([1-5])_retention_(\d+)_(\d+)\.csv$")
        for path in files:
            m = rx.match(path.name)
            if not m:
                issues.append(f"Filename doesn't match convention: {path.name}")
                continue
            zone = int(m.group(1))
            depth = float(f"{m.group(2)}.{m.group(3)}")
            seen.add((zone, depth))
            i, w = check_grid_csv(path, APPENDIX_O_HEADER, 0.0, 100.0,
                                  f"zone {zone} ret {depth:.2f}")
            issues.extend(i); warnings.extend(w)
        expected = {(z, d) for z in EXPECTED_ZONES for d in EXPECTED_RETENTION_DEPTHS}
        missing = expected - seen
        if missing:
            issues.append(f"Missing (zone, depth) combinations: {sorted(missing)}")

    # EMC
    emc_path = DATA_DIR / "reference" / "emc_table_9_2.csv"
    if not emc_path.exists():
        issues.append("Missing EMC table")
    else:
        with open(emc_path, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
        if rows[0] != EMC_HEADER:
            issues.append(f"EMC header mismatch: {rows[0]}")
        for i_row, row in enumerate(rows[1:], start=2):
            if len(row) != 3:
                issues.append(f"EMC row {i_row}: wrong column count")
                continue
            try:
                float(row[1]); float(row[2])
            except ValueError:
                issues.append(f"EMC row {i_row}: non-numeric: {row}")

    print("=" * 60)
    print("Data validation summary")
    print("=" * 60)
    print(f"Issues:   {len(issues)}")
    for x in issues:
        print(f"  ❌ {x}")
    print(f"Warnings: {len(warnings)}")
    for x in warnings[:20]:
        print(f"  ⚠ {x}")
    if len(warnings) > 20:
        print(f"  (... and {len(warnings)-20} more)")
    if not issues:
        print("\n✓ All data files validated cleanly.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
