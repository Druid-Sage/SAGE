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
tables.py — Data lookup and interpolation for FDEP AH Vol I tables.

This module loads three categories of lookup data from CSV files:

  1. Appendix N — Mean Annual Runoff Coefficients (ROC) as a function
     of meteorological zone, Non-DCIA Curve Number, and %DCIA.
     Used for converting catchment characteristics into annual runoff
     volume.

  2. Appendix O — Mean Annual Mass Removal Efficiencies for dry
     retention systems, as a function of zone, retention depth (inches
     over contributing area), Non-DCIA CN, and %DCIA.
     Used for computing dry retention BMP effectiveness.

  3. Table 9.2 — Standardized statewide stormwater nutrient EMC
     values (mg/L) for TN and TP by land use category.
     Used for default event mean concentrations when not overridden.

All three sources use bilinear interpolation in the (CN, DCIA) plane.
Appendix O additionally uses linear interpolation in the retention-depth
axis (between the discrete tabulated depths of 0.25, 0.50, ..., 4.00").

Why CSV files instead of hardcoded Python dicts?
  - The tables are large (~13,500 cells across 86 files).
  - Loading from CSV makes the data verifiable: an engineer can open
    a CSV, compare it to the AH PDF, and trust what they see.
  - Updating to a new AH version only requires re-transcribing CSVs;
    no Python code changes needed.

CSV format (enforced by validate_data.py):
  - Appendix N: 21 columns (CN + DCIA at 0, 5, 10, ..., 100).
  - Appendix O: 20 columns (CN + DCIA at 5, 10, ..., 100). No DCIA=0
    column because retention efficiencies are not defined at 0% DCIA
    in the source tables.
  - All values are numeric percentages or coefficients.

Reference: Florida DEP Applicant's Handbook Vol I, June 28, 2024,
Sections 9.2-9.5 and Appendices N and O.
"""

import csv
import math
import os
import re
from pathlib import Path


# Path to the data directory relative to this file.
# When the engine is shipped as a folder, all CSVs sit alongside the
# Python files, so pathlib resolves them correctly regardless of where
# the user runs the engine from.
_DATA_DIR = Path(__file__).parent / "data"

# Discrete retention depths that exist in Appendix O.
# These are the raw lookup keys; the engine interpolates linearly
# between them when a project's actual depth lands between two values.
RETENTION_DEPTHS = [0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00,
                    2.25, 2.50, 2.75, 3.00, 3.25, 3.50, 3.75, 4.00]

# Floor and ceiling for retention depth lookup. Outside this range the
# engine clamps to the nearest tabulated value and emits a warning.
RETENTION_DEPTH_MIN = 0.25
RETENTION_DEPTH_MAX = 4.00

# CN values present as rows in every Appendix N and O table.
CN_VALUES = [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98]

# DCIA columns vary between Appendix N (includes 0) and Appendix O.
DCIA_VALUES_N = list(range(0, 101, 5))   # 0, 5, 10, ..., 100
DCIA_VALUES_O = list(range(5, 101, 5))   # 5, 10, ..., 100


# ---------------------------------------------------------------------
# CSV loading helpers
# ---------------------------------------------------------------------

def _load_grid(path, expected_dcia):
    """Load a single CSV grid keyed by CN (rows) × DCIA (columns).

    Returns a dict: {cn_int: {dcia_int: value_float, ...}, ...}

    Skips empty cells (which are allowed when source PDF rows are
    missing — a real AH quirk in some Zone 4 / 5 tables).
    """
    grid = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)
    header = rows[0]
    # The first column header is "non_dcia_cn"; remaining are "dcia_<n>".
    # We trust the validation step has already confirmed correctness.
    dcia_cols = [int(h.split('_')[1]) for h in header[1:]]
    if dcia_cols != expected_dcia:
        raise ValueError(
            f"Unexpected DCIA columns in {path.name}: {dcia_cols}"
        )
    for row in rows[1:]:
        try:
            cn = int(row[0])
        except ValueError:
            continue  # skip rows with non-integer CN labels
        cn_dict = {}
        for dcia, cell in zip(dcia_cols, row[1:]):
            cell = cell.strip()
            if cell == "":
                continue  # missing cells are skipped (rare)
            cn_dict[dcia] = float(cell)
        grid[cn] = cn_dict
    return grid


# ---------------------------------------------------------------------
# Module-level caches: tables loaded lazily on first use
# ---------------------------------------------------------------------
# Lazy loading keeps engine startup fast (no need to load 80+ files
# before we know which zone the project uses) and makes testing easier
# (we can override _DATA_DIR for tests if needed).

_appendix_n_cache = {}      # zone -> grid dict
_appendix_o_cache = {}      # (zone, depth_str) -> grid dict
_emc_cache = None           # land_cover -> (tn_mgL, tp_mgL)


def _load_appendix_n(zone):
    """Load Appendix N for the given zone (1-5). Cached."""
    if zone in _appendix_n_cache:
        return _appendix_n_cache[zone]
    path = _DATA_DIR / "appendix_n_runoff_coefficients" / f"appendix_n_zone_{zone}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing Appendix N table: {path}")
    grid = _load_grid(path, DCIA_VALUES_N)
    _appendix_n_cache[zone] = grid
    return grid


def _depth_filename_part(depth):
    """Convert a numeric retention depth (e.g. 0.50) to the filename
    fragment used in our CSV naming convention (e.g. '0_50').

    The convention always uses two decimal places with the period
    replaced by an underscore: 0.25 -> '0_25', 1.00 -> '1_00'.
    """
    return f"{depth:.2f}".replace(".", "_")


def _load_appendix_o(zone, depth):
    """Load Appendix O for (zone, retention_depth). Cached.

    `depth` must be one of RETENTION_DEPTHS exactly. Callers that have
    a continuous-valued depth should use lookup_dry_retention_efficiency()
    which handles interpolation between adjacent tabulated depths.
    """
    if depth not in RETENTION_DEPTHS:
        raise ValueError(f"Depth {depth} not in tabulated set {RETENTION_DEPTHS}")
    key = (zone, depth)
    if key in _appendix_o_cache:
        return _appendix_o_cache[key]
    fname = f"appendix_o_zone_{zone}_retention_{_depth_filename_part(depth)}.csv"
    path = _DATA_DIR / "appendix_o_dry_retention" / fname
    if not path.exists():
        raise FileNotFoundError(f"Missing Appendix O table: {path}")
    grid = _load_grid(path, DCIA_VALUES_O)
    _appendix_o_cache[key] = grid
    return grid


def _load_emc():
    """Load Table 9.2 EMC values, cached.

    Returns dict: {land_cover_str: (tn_mgL, tp_mgL)}
    """
    global _emc_cache
    if _emc_cache is not None:
        return _emc_cache
    path = _DATA_DIR / "reference" / "emc_table_9_2.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing EMC table: {path}")
    emc = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        # The CSV column name is 'land_use' (matching FDEP Table 9.2),
        # but in our schema the field is called 'land_cover' to match
        # how engineers commonly refer to it. Both terms are valid.
        for row in reader:
            emc[row['land_use']] = (float(row['tn_mgL']), float(row['tp_mgL']))
    _emc_cache = emc
    return emc


# ---------------------------------------------------------------------
# Bilinear interpolation across a (CN, DCIA) grid
# ---------------------------------------------------------------------

def _bilinear(grid, cn, dcia):
    """Bilinearly interpolate a value at (cn, dcia) within `grid`.

    `grid` is the dict returned by _load_grid: {cn_int: {dcia_int: val}}.

    Strategy:
      1. Find the two tabulated CN rows that bracket the requested cn
         (clamping to the table edges if cn is outside the range).
      2. Same for DCIA columns.
      3. Look up the four corner values, linearly interpolate twice
         in DCIA, then once in CN.

    Returns a float. Raises KeyError if any of the four bracketing
    cells is missing from the grid (shouldn't happen with our data
    but the error is informative if it ever does).
    """
    # Identify the available CN rows actually present in this grid.
    # We use the grid's own keys rather than the global CN_VALUES list
    # because some grids have missing rows in the source PDF.
    cn_rows = sorted(grid.keys())
    cn_lo, cn_hi = _bracket(cn_rows, cn)

    # DCIA columns we'll interpolate within. The two CN rows might
    # have different available DCIA columns in principle; in practice
    # they're identical, but we use the intersection to be safe.
    dcia_cols_lo = sorted(grid[cn_lo].keys())
    dcia_cols_hi = sorted(grid[cn_hi].keys())
    dcia_cols = sorted(set(dcia_cols_lo) & set(dcia_cols_hi))
    dcia_lo, dcia_hi = _bracket(dcia_cols, dcia)

    # Four corner values
    v_lo_lo = grid[cn_lo][dcia_lo]
    v_lo_hi = grid[cn_lo][dcia_hi]
    v_hi_lo = grid[cn_hi][dcia_lo]
    v_hi_hi = grid[cn_hi][dcia_hi]

    # Interpolate in DCIA at each CN row
    if dcia_hi == dcia_lo:
        v_lo = v_lo_lo
        v_hi = v_hi_lo
    else:
        f_dcia = (dcia - dcia_lo) / (dcia_hi - dcia_lo)
        v_lo = v_lo_lo + f_dcia * (v_lo_hi - v_lo_lo)
        v_hi = v_hi_lo + f_dcia * (v_hi_hi - v_hi_lo)

    # Interpolate in CN
    if cn_hi == cn_lo:
        return v_lo
    f_cn = (cn - cn_lo) / (cn_hi - cn_lo)
    return v_lo + f_cn * (v_hi - v_lo)


def _bracket(values, target):
    """Find the two values from sorted list `values` that bracket `target`.

    If target is below the min, returns (min, min). If above max,
    returns (max, max). Otherwise returns the pair (lo, hi) with
    lo <= target <= hi.

    Used by interpolation routines to handle out-of-range inputs by
    clamping rather than raising, which matches AH practice (the
    tables only define a finite range).
    """
    if target <= values[0]:
        return values[0], values[0]
    if target >= values[-1]:
        return values[-1], values[-1]
    for i in range(len(values) - 1):
        if values[i] <= target <= values[i+1]:
            return values[i], values[i+1]
    # Shouldn't reach here given the bounds checks above
    return values[-1], values[-1]


# ---------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------

def lookup_roc(zone, cn, dcia_pct):
    """Look up the Mean Annual Runoff Coefficient (ROC) from Appendix N.

    Args:
      zone: integer 1-5 (meteorological zone)
      cn: float Non-DCIA Curve Number (typically 30-98)
      dcia_pct: float %DCIA (0-100)

    Returns:
      float ROC (typically 0.0 to 0.85), used as the fraction of
      annual rainfall that becomes runoff.

    Out-of-range CN or DCIA values are clamped to the table edges
    (no extrapolation) — this matches the AH's intent that the tables
    represent the full range of physically reasonable inputs.
    """
    grid = _load_appendix_n(zone)
    return _bilinear(grid, cn, dcia_pct)


def lookup_dry_retention_efficiency(zone, cn, dcia_pct, retention_depth_in):
    """Look up dry retention efficiency from Appendix O.

    Args:
      zone: integer 1-5
      cn: float Non-DCIA Curve Number
      dcia_pct: float %DCIA
      retention_depth_in: float storage volume (inches over contributing area)

    Returns:
      (efficiency_pct, warnings) tuple where:
        efficiency_pct: float 0-100 (mass removal %)
        warnings: list of human-readable warning strings

    Behavior:
      - retention_depth < 0.25" → clamp to 0.25" with warning
      - retention_depth > 4.00" → clamp to 4.00" with warning
      - 0.25" ≤ depth < 0.50" → use the table at this depth, but warn
        "below FDEP minimum PAV of 0.5 inches"
      - 0.50" ≤ depth ≤ 4.00" → standard lookup with linear interpolation
        between the two adjacent tabulated depths

    The (CN, DCIA) interpolation is bilinear within each adjacent
    table. The retention-depth interpolation is linear between two
    bilinearly-interpolated values.
    """
    warnings = []
    depth = retention_depth_in

    # Floor at 0.25" — the smallest tabulated value
    if depth < RETENTION_DEPTH_MIN:
        warnings.append(
            f"Retention depth {depth:.3f}\" is below the smallest tabulated "
            f"depth ({RETENTION_DEPTH_MIN}\"). Calculation uses the {RETENTION_DEPTH_MIN}\" "
            f"table as a floor; the actual efficiency may be lower."
        )
        depth = RETENTION_DEPTH_MIN

    # Ceiling at 4.00" — extrapolation above this isn't supported by the AH
    if depth > RETENTION_DEPTH_MAX:
        warnings.append(
            f"Retention depth {depth:.3f}\" is above the largest tabulated "
            f"depth ({RETENTION_DEPTH_MAX}\"). Calculation uses the {RETENTION_DEPTH_MAX}\" "
            f"table as a ceiling."
        )
        depth = RETENTION_DEPTH_MAX

    # Below FDEP minimum PAV but still within tabulated range
    # Tolerance of 1e-3 prevents spurious warnings from rounding
    # (e.g., storage 0.833 ac-ft over 20 ac → depth 0.4998")
    if depth < 0.50 - 1e-3:
        warnings.append(
            f"Retention depth {depth:.3f}\" is below the FDEP minimum "
            f"Pollution Abatement Volume of 0.5 inches over the contributing "
            f"catchment area. The pond may not satisfy AH §9.5 / Appendix O "
            f"design requirements; verify pond design separately."
        )

    # Find the two tabulated depths that bracket the actual depth
    depth_lo, depth_hi = _bracket(RETENTION_DEPTHS, depth)

    # Bilinear at each depth
    grid_lo = _load_appendix_o(zone, depth_lo)
    eff_lo = _bilinear(grid_lo, cn, dcia_pct)
    if depth_hi == depth_lo:
        return eff_lo, warnings
    grid_hi = _load_appendix_o(zone, depth_hi)
    eff_hi = _bilinear(grid_hi, cn, dcia_pct)

    # Linear interpolation in the retention-depth axis
    f = (depth - depth_lo) / (depth_hi - depth_lo)
    return eff_lo + f * (eff_hi - eff_lo), warnings


def lookup_emc(land_cover):
    """Look up (TN_mgL, TP_mgL) from Table 9.2 for a land cover string.

    Returns a tuple (tn_mgL, tp_mgL).

    Raises KeyError with a helpful message if the land cover isn't
    recognized — except for the literal string "Custom", which raises
    a different error to remind the caller they need to provide
    overrides.
    """
    emc = _load_emc()
    if land_cover == "Custom":
        raise ValueError(
            "Land cover 'Custom' requires both emc_tn_override and "
            "emc_tp_override to be specified on the subarea."
        )
    if land_cover not in emc:
        valid = sorted(emc.keys())
        raise KeyError(
            f"Unknown land_cover '{land_cover}'. "
            f"Valid options: {valid} or 'Custom' (with EMC overrides)."
        )
    return emc[land_cover]


def list_land_covers():
    """Return the sorted list of land cover names in Table 9.2."""
    return sorted(_load_emc().keys())
