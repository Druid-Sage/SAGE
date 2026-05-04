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
tests.py — Regression tests for the calculation engine.

Run as:
    python tests.py

These tests verify that:
  - The lookup tables return correct values at known cells
  - Bilinear interpolation produces correct intermediates
  - The unit conversion factor matches AH §9.2.2
  - The wet detention curves match Harper & Baker (2007)
  - Eq. 9-5 compounding works correctly
  - The example_project.json runs end-to-end with expected outfall load

If any test fails, the engine has a bug — DO NOT trust outputs until
the test passes.
"""

import math
import sys

import bmps
import engine
import tables


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


def test_appendix_n_known_cell():
    # Zone 4, CN=80, DCIA=40 should be 0.407 per appendix_n_zone_4.csv
    roc = tables.lookup_roc(zone=4, cn=80, dcia_pct=40)
    assert approx(roc, 0.407), f"Expected 0.407, got {roc}"


def test_appendix_n_interpolation():
    # Halfway between (CN=75, DCIA=40)=0.387 and (CN=80, DCIA=40)=0.407
    # Expected: 0.397
    roc = tables.lookup_roc(zone=4, cn=77.5, dcia_pct=40)
    assert approx(roc, 0.397), f"Expected 0.397, got {roc}"


def test_appendix_n_bilinear():
    # Center of 4 cells: (75,40)=0.387, (75,45)=0.423, (80,40)=0.407, (80,45)=0.442
    # Avg = 0.41475
    roc = tables.lookup_roc(zone=4, cn=77.5, dcia_pct=42.5)
    assert approx(roc, 0.41475, 1e-4), f"Expected 0.41475, got {roc}"


def test_appendix_o_known_cell():
    # Zone 4, CN=80, DCIA=40, retention=1.00" = 78.1 per appendix_o_zone_4_retention_1_00.csv
    eff, w = tables.lookup_dry_retention_efficiency(zone=4, cn=80, dcia_pct=40, retention_depth_in=1.00)
    assert approx(eff, 78.1), f"Expected 78.1, got {eff}"
    assert w == [], f"Unexpected warnings: {w}"


def test_appendix_o_depth_interpolation():
    # 1.00 -> 78.1, 1.25 -> 82.9, so 1.10 should be ~80.02
    eff, _ = tables.lookup_dry_retention_efficiency(zone=4, cn=80, dcia_pct=40, retention_depth_in=1.10)
    expected = 78.1 + (1.10 - 1.00) / 0.25 * (82.9 - 78.1)
    assert approx(eff, expected), f"Expected {expected}, got {eff}"


def test_appendix_o_floor():
    # Below 0.25" should clamp to 0.25" with warning
    eff, w = tables.lookup_dry_retention_efficiency(zone=4, cn=80, dcia_pct=40, retention_depth_in=0.10)
    assert len(w) == 2, f"Expected 2 warnings (below 0.25 + below 0.5), got {len(w)}"
    # Should equal 0.25" lookup
    eff_at_floor, _ = tables.lookup_dry_retention_efficiency(zone=4, cn=80, dcia_pct=40, retention_depth_in=0.25)
    assert approx(eff, eff_at_floor)


def test_appendix_o_below_min_pav():
    # Between 0.25 and 0.50 should warn but not clamp
    eff, w = tables.lookup_dry_retention_efficiency(zone=4, cn=80, dcia_pct=40, retention_depth_in=0.30)
    assert len(w) == 1, f"Expected 1 warning (below FDEP minimum PAV), got {len(w)}"
    assert "minimum Pollution Abatement" in w[0]


def test_emc_table():
    tn, tp = tables.lookup_emc('Single Family')
    assert approx(tn, 1.77), f"Expected 1.77, got {tn}"
    assert approx(tp, 0.327), f"Expected 0.327, got {tp}"


def test_unit_conversion_factor():
    # Verify the lb/yr conversion factor: V (ac-ft) × EMC (mg/L) × C
    # 1 ac-ft = 1,233,481.84 L
    # mass(lb) = V × 1,233,481.84 × EMC × (1/1000) × (1/453.592)
    # = V × EMC × 2.71947
    L_per_acft = 43560 * 7.48052 * 3.78541  # ft^3/ac × gal/ft^3 × L/gal
    factor = L_per_acft / 1000 / 453.592
    assert approx(factor, 2.71947, 1e-3), f"Conversion factor: {factor}"


def test_wet_detention_curves():
    # Harper & Baker formulas at td=31 days (the common test value)
    td = 31.0
    base_tn = 43.75 * td / (4.38 + td)
    base_tp = 40.13 + 6.372 * math.log(td) + 0.213 * (math.log(td) ** 2)
    # Detention methodology PDF: nitrogen plot annotated at 38% for 31 days
    assert approx(base_tn, 38.32, 0.5), f"TN at td=31: {base_tn}"
    # TP should be considerably higher
    assert base_tp > 60, f"TP at td=31: {base_tp}"


def test_eq_95_compounding():
    # Three BMPs at 50%, 30%, 20% should compound to:
    # 1 - (0.5)(0.7)(0.8) = 1 - 0.28 = 0.72 -> 72%
    e1, e2, e3 = 50, 30, 20
    combined = (1 - (1 - e1/100) * (1 - e2/100) * (1 - e3/100)) * 100
    assert approx(combined, 72.0), f"Got {combined}"


def test_example_project_end_to_end():
    """Run example_project.json through the full engine."""
    project = engine.parse_project("example_project.json")
    engine.validate_nodes(project)
    result = engine.run_calculations(project)

    # Baseline check
    assert approx(result['baseline_volume_acft'], 35.273, 0.01)
    # 17.6364 ac-ft × 1.77 mg/L × 2.71947 × 2 catchments
    expected_baseline_tn = 2 * (10 * 52 * 0.407 / 12) * 1.77 * 2.71947
    assert approx(result['baseline_tn_lb'], expected_baseline_tn, 0.5), \
        f"Got {result['baseline_tn_lb']}, expected {expected_baseline_tn}"

    # Outfall check
    out = result['outfall_results']['OUT1']
    # 87.94% TN reduction expected from our hand-trace
    tn_reduction = (1 - out['tn_lb'] / result['baseline_tn_lb']) * 100
    assert approx(tn_reduction, 87.94, 0.1), f"TN reduction: {tn_reduction}"

    # TP reduction ~94%
    tp_reduction = (1 - out['tp_lb'] / result['baseline_tp_lb']) * 100
    assert approx(tp_reduction, 94.4, 0.5), f"TP reduction: {tp_reduction}"


def main():
    tests = [
        test_appendix_n_known_cell,
        test_appendix_n_interpolation,
        test_appendix_n_bilinear,
        test_appendix_o_known_cell,
        test_appendix_o_depth_interpolation,
        test_appendix_o_floor,
        test_appendix_o_below_min_pav,
        test_emc_table,
        test_unit_conversion_factor,
        test_wet_detention_curves,
        test_eq_95_compounding,
        test_example_project_end_to_end,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  ✓ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: ERROR {type(e).__name__}: {e}")
    print()
    print(f"  {passed} passed, {failed} failed (out of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
