# SAGE — Project File Schema (v1)

This document specifies the structure of project files for SAGE
(Stormwater Analysis &amp; GSI Evaluator). The same schema is used by
the CLI engine, the HTTP server API, and the web UI's Save/Load.

Project files are JSON. The engine reads them, computes loads and
removals, and writes an HTML audit report.

The schema is the contract between the user-facing layer (whether that's
a hand-edited JSON file or a graphical editor in V2) and the calculation
engine. The engine validates every project file against this schema
before computing anything; invalid files produce clear errors pointing
to the offending field.

---

## Top-level structure

```json
{
  "schema_version": "1.0",
  "project": { ... },
  "nodes": [ ... ]
}
```

A project file holds a single project. The graph is one flat list of
nodes; connectivity is encoded by each node's `drains_to` field rather
than by a separate edges array. There is no concept of "scenarios" — if
a user wants to compare pre vs. post, they make two project files.

`schema_version` is a string. The engine refuses to load a file whose
schema version it doesn't recognize. Bumping this number is how we
evolve the format without breaking existing project files.

---

## Project metadata

```json
"project": {
  "name": "Example Project",
  "applicant": "ACME Engineering",
  "date_created": "2025-05-02",
  "date_modified": "2025-05-02",
  "zone": 4,
  "annual_rainfall_in": 52.0,
  "default_units": "lb_per_yr",
  "notes": "Free-text project description, site context, etc."
}
```

| Field | Type | Notes |
|---|---|---|
| `name` | string | Required. Used in report header. |
| `applicant` | string | Optional. Used in report header. |
| `date_created` | string (ISO 8601) | Set on file creation; user-editable. |
| `date_modified` | string (ISO 8601) | Engine updates on every run; user-editable. |
| `zone` | integer 1-5 | Required. Determines which Appendix N and O tables to use. |
| `annual_rainfall_in` | float > 0 | Project-wide annual rainfall (in). From AH §9.4 / Appendix M isopleths. |
| `default_units` | string | `"lb_per_yr"` or `"kg_per_yr"`. Display preference; reports always show both. |
| `notes` | string | Free text. Appears at top of audit report. |

---

## Nodes

Every node has these common fields:

```json
{
  "id": "<unique string>",
  "type": "<catchment | dry_retention | wet_detention | other_bmp | outfall>",
  "name": "<human-readable label>",
  "notes": "<free text, appears in audit report>",
  "drains_to": "<id of the downstream node, or null>",
  "position": { "x": 0.0, "y": 0.0 }
}
```

`id` must be unique within the project. Convention: `C1`, `C2` for
catchments; `B1`, `B2` for BMPs; `OUT1`, `OUT2` for outfalls. The
engine accepts any unique non-empty string.

`drains_to` is the id of the single downstream node this node feeds,
or `null` if the node is unconnected (which the engine warns about).
Outfalls do not have a `drains_to` field (or it must be `null` if
present).

`position` is for the V2 graphical editor — where the node sits on the
canvas. V1 engine preserves but ignores it. If absent, the engine
defaults it to `{ "x": 0, "y": 0 }`.

Type-specific fields are documented below.

### Type: `catchment`

```json
{
  "id": "C1",
  "type": "catchment",
  "name": "North Parking Lot",
  "notes": "Primary commercial catchment",
  "drains_to": "B1",
  "position": { "x": 100, "y": 200 },
  "subareas": [
    {
      "land_cover": "High Intensity Commercial",
      "area_ac": 6.0,
      "non_dcia_cn": 80.00,
      "dcia_pct": 70.00,
      "emc_tn_override": null,
      "emc_tp_override": null,
      "notes": ""
    },
    {
      "land_cover": "Single Family",
      "area_ac": 4.0,
      "non_dcia_cn": 75.00,
      "dcia_pct": 30.00,
      "emc_tn_override": null,
      "emc_tp_override": null,
      "notes": "Half-acre lots, mature trees"
    }
  ]
}
```

A catchment has one or more subareas. Each subarea has its own land
cover, area, CN, DCIA, and (optionally) EMC overrides.

| Subarea field | Type | Notes |
|---|---|---|
| `land_cover` | string | Must match an entry in EMC table or the literal `"Custom"`. Drives default EMC values. |
| `area_ac` | float > 0 | Subarea area in acres. |
| `non_dcia_cn` | float 30.00-98.00 | Non-DCIA Curve Number, two decimal places. Bilinear-interpolated against Appendix N. |
| `dcia_pct` | float 0.00-100.00 | %DCIA for this subarea, two decimal places. |
| `emc_tn_override` | float ≥ 0 or `null` | If non-null, used instead of the table value. mg/L. |
| `emc_tp_override` | float ≥ 0 or `null` | Same for phosphorus. |
| `notes` | string | Free text, appears in audit report. |

**Land cover and EMC.** When `land_cover` matches an entry in the EMC
table (e.g., "Single Family"), the engine looks up EMCs from the table.
When `land_cover` is `"Custom"`, both EMC overrides MUST be non-null.
The engine errors out if `"Custom"` is used without overrides.

**A catchment is both a load source and a junction.** Runoff and load
generated by the catchment's own subareas combine with any incoming flow
from upstream nodes (whose `drains_to` points at this catchment). The
catchment's discharge — via its own `drains_to` — carries the sum: own
+ upstream. This is what enables `C1 → C2 → BMP1` without a separate
junction node type.

**Composite CN/DCIA for downstream BMPs.** When a downstream BMP needs
composite CN and DCIA (dry retention does; wet detention doesn't), the
engine area-weights across all subareas of all upstream catchments
contributing to that BMP's input. Subareas in catchments whose path to
the BMP passes through an upstream dry-retention BMP get DCIA → 0 (the
D-prime rule we agreed on). CN is preserved per-subarea regardless of
upstream history.

### Type: `dry_retention`

```json
{
  "id": "B1",
  "type": "dry_retention",
  "name": "North Pond",
  "notes": "Includes 0.4 ac-ft of unsaturated soil storage",
  "drains_to": "B2",
  "position": { "x": 300, "y": 150 },
  "storage_volume_acft": 0.833
}
```

| Field | Type | Notes |
|---|---|---|
| `storage_volume_acft` | float > 0 | Total treatment volume: storage above pond bottom + effective unsaturated soil to seasonal high water table. |

The engine computes:
- Contributing area = sum of all upstream catchment subarea areas
- Retention depth (in) = `storage_volume_acft × 12 / contributing_area_ac`
- Composite CN, DCIA at this BMP per the D-prime rule
- Efficiency from Appendix O at (zone, CN, DCIA, depth) with bilinear
  interpolation in (CN, DCIA) and linear interpolation in retention
  depth between adjacent table depths.

Engine warnings (don't block the calculation):
- Retention depth < 0.50": flag as "below FDEP minimum PAV"
- Retention depth < 0.25": clamp to 0.25" for the lookup, flag as
  "below smallest tabulated retention depth — using 0.25\" floor"
- Retention depth > 4.00": clamp to 4.00", flag as "above largest
  tabulated retention depth — using 4.00\" ceiling"

### Type: `wet_detention`

```json
{
  "id": "B2",
  "type": "wet_detention",
  "name": "Main Wet Pond",
  "notes": "Existing pond, expanded for this project",
  "drains_to": "OUT1",
  "position": { "x": 500, "y": 200 },
  "permanent_pool_acft": 2.0,
  "littoral_zone_pct": 10.0,
  "littoral_zone_notes": "Per AH Vol II, max 10% credit. Verify pond meets minimum littoral area requirement.",
  "floating_islands_pct_coverage": 0.0,
  "floating_islands_credit_override": null,
  "floating_islands_notes": "",
  "alum_injection_credit_tn_pct": 0.0,
  "alum_injection_credit_tp_pct": 0.0,
  "alum_injection_notes": ""
}
```

| Field | Type | Notes |
|---|---|---|
| `permanent_pool_acft` | float > 0 | Permanent pool volume. |
| `littoral_zone_pct` | float ≥ 0 | TN/TP credit (%). Defaults to 0. AH suggests max 10% but engineer can justify more (notes field). |
| `littoral_zone_notes` | string | Justification for credit value. |
| `floating_islands_pct_coverage` | float 0-100 | If ≥ 5.0, default credit of 12% TN and 12% TP applies. |
| `floating_islands_credit_override` | float or `null` | If non-null, overrides the auto-computed 12% credit. |
| `floating_islands_notes` | string | Free text. |
| `alum_injection_credit_tn_pct` | float ≥ 0 | TN credit from alum injection. |
| `alum_injection_credit_tp_pct` | float ≥ 0 | TP credit from alum injection. |
| `alum_injection_notes` | string | Jar testing study citation. |

Engine computes:
- Total annual inflow V (ac-ft/yr) = sum of inflow from upstream
- td (days) = `permanent_pool_acft / V × 365`
- Base TN efficiency: `43.75 × td / (4.38 + td)` (%)
- Base TP efficiency: `40.13 + 6.372 × ln(td) + 0.213 × (ln td)^2` (%)
- Effective floating-islands credit: 12% if coverage ≥ 5% and override
  is null; else override; else 0
- Combined efficiency via Eq. 9-5 compounding:
  `Eff_total = 1 - (1 - base) × (1 - littoral) × (1 - floating) × (1 - alum)`
- TN and TP computed independently with their own credits

Engine warnings:
- td > 200 days: flag as "residence time exceeds typical design range; verify pond sizing"
- Wet pond directly downstream of another wet pond: flag the pair with
  "FDEP methodology recommends summing residence times for wet ponds in
  series with no input between them. This calculation treats them as
  independent. If you want td summation, combine the two ponds into one
  with the summed permanent pool."

Wet detention does not change annual volume (inflow ≈ outflow).

### Type: `other_bmp`

```json
{
  "id": "B3",
  "type": "other_bmp",
  "name": "Pretreatment Baffle Box",
  "notes": "Suntree Nutrient Separating Baffle Box, 2nd gen",
  "drains_to": "B2",
  "position": { "x": 200, "y": 100 },
  "preset": "baffle_box_2nd_gen",
  "tn_removal_pct": 19.05,
  "tp_removal_pct": 15.50
}
```

| Field | Type | Notes |
|---|---|---|
| `preset` | string | One of the preset keys (see below) or `"custom"`. |
| `tn_removal_pct` | float ≥ 0 | TN removal (%). Auto-populated when preset is selected, but always overridable. |
| `tp_removal_pct` | float ≥ 0 | TP removal (%). Same behavior. |

**Available presets** (engine looks up the corresponding default %s but
always uses what's in `tn_removal_pct` / `tp_removal_pct` for the actual
calculation — preset is just a UI helper to populate defaults):

| Preset key | Default TN % | Default TP % | Source |
|---|---|---|---|
| `baffle_box_1st_gen` | 0.50 | 2.30 | App. O, GPI 2010 / UCF for FDOT / Casselberry |
| `baffle_box_2nd_gen` | 19.05 | 15.50 | App. O, same sources |
| `hydrodynamic_separator` | 0.0 | 10.0 | App. O, Sanford Stormceptor 2008 / Broadway Outfall 2006 |
| `custom` | 0.0 | 0.0 | User enters everything; notes field should explain. |

The engine applies these as flat percentages: load_out = load_in × (1 − pct/100).
Volume passes through unchanged.

If the preset is `"custom"`, the engine emits a warning if the notes
field is empty, prompting the engineer to document the source.

### Type: `outfall`

```json
{
  "id": "OUT1",
  "type": "outfall",
  "name": "Discharge to Lake Smith",
  "notes": "Per FDEP permit",
  "position": { "x": 700, "y": 200 }
}
```

Outfalls have no parameters and no `drains_to`. They're terminal nodes
that record the final volume and load reaching them. A project may
have multiple outfalls (e.g., a project that discharges to two
different receiving waters). Engine reports per-outfall and total.

---

## Constraints the engine enforces

- Every `id` is unique within the project.
- Every `drains_to` (when non-null) refers to an existing node.
- Outfalls have no `drains_to`.
- Catchments and BMPs have `drains_to` (`null` allowed but warns).
- The graph is a DAG: following `drains_to` from any node must eventually
  terminate at an outfall (or `null`). Cycles produce a hard error
  identifying the cycle.
- Every catchment must reach an outfall via `drains_to` chains (otherwise
  its load goes nowhere — engine warns).

---

## What the engine outputs

For every project run, the engine produces:

1. **Console summary** — quick numeric totals to stdout:
   - Per outfall: name, volume (ac-ft/yr), TN load (lb/yr, kg/yr), TP load (lb/yr, kg/yr)
   - Project totals across all outfalls
   - Total catchment-generated load (the unrouted baseline)
   - Overall TN reduction %, TP reduction %
   - List of warnings (if any)

2. **HTML audit report** — written next to the project file with a `_report.html`
   suffix. Contains:
   - Project metadata and notes
   - Per-catchment runoff and load calculation with Appendix N citations
     and per-subarea breakdown
   - Per-BMP calculation with cited equation/table, including upstream
     load and volume, computed efficiency, residual load and volume
   - Junction mass balances at any node receiving multiple inputs
   - Final per-outfall summary table
   - Total reduction % comparing baseline (sum of catchment loads) to
     outfall totals
   - All warnings, with offending node(s) named
   - All notes from catchments, BMPs, and project metadata

---

## What's NOT in V1 schema (deferred)

These would be schema additions, not changes — adding them later won't
break existing project files.

- **Splits / bypass ratios** — V1 has single `drains_to`. V2 might extend
  to a list of `{target, fraction}` entries.
- **Project libraries / multiple projects per file.** V1 is one project
  per file.
- **Performance-standard auto-checking.** V1 reports % removal; user
  judges against their HUC12 / OFW / impaired requirement.
- **Pre/post comparison automation.** V1 user makes two project files.
