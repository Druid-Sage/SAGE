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
bmps.py — BMP (Best Management Practice) processing classes.

Each BMP type implements `process(inflow, context)` which:
  - Takes an Inflow object (volume + TN load + TP load + provenance metadata)
  - Returns an Outflow (same fields, with reductions applied)
  - Records a TraceEntry describing what it did, for the audit report

Provenance: the inflow tracks per-catchment "streams" — when load enters
a junction or BMP, we keep separate tags showing how much load originated
from which upstream catchment, and which dry-retention BMPs it has
already passed through. This is what enables the D-prime rule for
composite CN/DCIA at downstream dry retention BMPs.

The BMP types implemented here:
  - DryRetention: Appendix O lookup based on storage volume and
    composite catchment characteristics.
  - WetDetention: residence-time formulas with optional littoral,
    floating islands, and alum credits compounded via Eq. 9-5.
  - OtherBMP: flat-percentage removal (baffle boxes, separators,
    custom user-specified).

Reference equations:
  - Eq. 9-5 (treatment train): Eff_total = 1 - Π(1 - Eff_i)
  - Wet detention TN: 43.75 * td / (4.38 + td)  (R² = 0.800)
  - Wet detention TP: 40.13 + 6.372*ln(td) + 0.213*(ln td)²  (R² = 0.897)
"""

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import tables


# ---------------------------------------------------------------------
# Data classes for stream-level provenance tracking
# ---------------------------------------------------------------------

@dataclass
class StreamComponent:
    """One catchment's contribution to a flow.

    A flow into a BMP may consist of contributions from multiple
    upstream catchments. Each contribution remembers:
      - which catchment it originated from (for audit clarity)
      - the original area, CN, and DCIA of that catchment
        (for composite-CN/DCIA calculations at downstream dry BMPs)
      - whether DCIA should be treated as zero because of upstream
        dry retention (the D-prime rule)
      - current volume and load values, after any upstream treatment
    """
    source_catchment_id: str
    area_ac: float            # Total area of the originating catchment
    composite_cn: float       # Area-weighted CN of the originating catchment
    composite_dcia: float     # Area-weighted DCIA, possibly zeroed if upstream had dry retention
    dcia_zeroed: bool         # True if DCIA was reset to 0 by upstream dry retention
    volume_acft: float        # Current annual volume from this catchment
    load_tn_lb: float         # Current annual TN load (lb/yr) from this catchment
    load_tp_lb: float         # Current annual TP load (lb/yr) from this catchment


@dataclass
class Flow:
    """A flow on a graph edge. List of stream components, one per
    contributing catchment.
    """
    components: List[StreamComponent] = field(default_factory=list)

    def total_volume(self):
        return sum(c.volume_acft for c in self.components)

    def total_tn(self):
        return sum(c.load_tn_lb for c in self.components)

    def total_tp(self):
        return sum(c.load_tp_lb for c in self.components)

    def composite_cn(self):
        """Area-weighted composite CN across all components.

        Used by dry retention BMPs for the Appendix O lookup.
        """
        total_area = sum(c.area_ac for c in self.components)
        if total_area == 0:
            return 0
        return sum(c.composite_cn * c.area_ac for c in self.components) / total_area

    def composite_dcia(self):
        """Area-weighted composite DCIA across all components.

        Components whose DCIA was zeroed by upstream dry retention
        contribute 0 here. This is the D-prime rule.
        """
        total_area = sum(c.area_ac for c in self.components)
        if total_area == 0:
            return 0
        return sum(c.composite_dcia * c.area_ac for c in self.components) / total_area

    def total_area(self):
        return sum(c.area_ac for c in self.components)


# ---------------------------------------------------------------------
# Trace entries: one per node, drives the audit report
# ---------------------------------------------------------------------

@dataclass
class TraceEntry:
    """A record of what one node did during graph traversal."""
    node_id: str
    node_type: str
    node_name: str
    node_notes: str
    inputs_summary: dict        # {volume_acft, tn_lb, tp_lb, components: [...]}
    outputs_summary: dict       # same shape
    calculation_details: dict   # type-specific: efficiency, td, table refs, etc.
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Catchment processing (not a BMP, but uses the same interface)
# ---------------------------------------------------------------------

def process_catchment(node, upstream_flow, project_meta):
    """Compute runoff and load for one catchment and combine with upstream.

    Args:
      node: the catchment node dict (from JSON)
      upstream_flow: Flow object (may be empty if no upstream nodes)
      project_meta: project-level metadata (zone, rainfall, etc.)

    Returns:
      (output_flow, trace_entry)

    The catchment generates its own Flow components from its subareas
    using Appendix N for runoff coefficient and either Table 9.2 EMC
    or user-specified overrides for concentration. Then it merges
    those components with the upstream Flow's components to produce
    a combined output Flow.
    """
    zone = project_meta['zone']
    rainfall = project_meta['annual_rainfall_in']
    warnings = []

    own_components = []
    subarea_traces = []
    own_total_area = 0.0
    own_weighted_cn_num = 0.0
    own_weighted_dcia_num = 0.0

    for sub in node['subareas']:
        area = sub['area_ac']
        cn = sub['non_dcia_cn']
        dcia = sub['dcia_pct']
        land_cover = sub['land_cover']

        # Determine EMC values: use overrides if provided, else Table 9.2 lookup
        if land_cover == 'Custom':
            if sub.get('emc_tn_override') is None or sub.get('emc_tp_override') is None:
                raise ValueError(
                    f"Catchment {node['id']} has subarea with land_cover='Custom' "
                    f"but missing emc_tn_override or emc_tp_override."
                )
            tn_emc = sub['emc_tn_override']
            tp_emc = sub['emc_tp_override']
            emc_source = "User-specified (Custom)"
        else:
            default_tn, default_tp = tables.lookup_emc(land_cover)
            tn_emc = sub['emc_tn_override'] if sub.get('emc_tn_override') is not None else default_tn
            tp_emc = sub['emc_tp_override'] if sub.get('emc_tp_override') is not None else default_tp
            emc_source = (f"Table 9.2 default" if sub.get('emc_tn_override') is None
                          else f"User override (default was Table 9.2)")

        # Look up runoff coefficient from Appendix N
        roc = tables.lookup_roc(zone, cn, dcia)

        # Annual runoff volume (ac-ft) = area (ac) × rainfall (in) × ROC × (1 ft / 12 in)
        # (AH §9.2.1 Eq. 9-1)
        volume = area * rainfall * roc / 12.0

        # Mass loading (lb/yr) = volume (ac-ft) × EMC (mg/L) × 2.71947 conversion
        # Conversion derivation:
        #   1 ac-ft = 43,560 ft² × 1 ft = 43,560 ft³
        #   = 43,560 × 7.48 gal = 325,829 gal
        #   = 325,829 × 3.785 L = 1,233,464 L
        #   mass (lb) = V (ac-ft) × 1,233,464 L/ac-ft × EMC (mg/L) × (1 g / 1000 mg) × (1 lb / 453.592 g)
        #             = V × EMC × 2.71947
        # (AH §9.2.2 Eq. 9-2)
        ACFT_TO_LB_FACTOR = 2.71947
        load_tn = volume * tn_emc * ACFT_TO_LB_FACTOR
        load_tp = volume * tp_emc * ACFT_TO_LB_FACTOR

        own_total_area += area
        own_weighted_cn_num += cn * area
        own_weighted_dcia_num += dcia * area

        subarea_traces.append({
            'land_cover': land_cover,
            'area_ac': area,
            'non_dcia_cn': cn,
            'dcia_pct': dcia,
            'roc': roc,
            'tn_emc_mgL': tn_emc,
            'tp_emc_mgL': tp_emc,
            'emc_source': emc_source,
            'volume_acft': volume,
            'load_tn_lb': load_tn,
            'load_tp_lb': load_tp,
            'notes': sub.get('notes', ''),
        })

    # Catchment-level composite CN/DCIA (area-weighted across this catchment's subareas)
    composite_cn = own_weighted_cn_num / own_total_area
    composite_dcia = own_weighted_dcia_num / own_total_area
    own_volume = sum(s['volume_acft'] for s in subarea_traces)
    own_tn = sum(s['load_tn_lb'] for s in subarea_traces)
    own_tp = sum(s['load_tp_lb'] for s in subarea_traces)

    # Build a single StreamComponent for this catchment's contribution.
    # Each catchment is its own provenance tag downstream.
    own_component = StreamComponent(
        source_catchment_id=node['id'],
        area_ac=own_total_area,
        composite_cn=composite_cn,
        composite_dcia=composite_dcia,
        dcia_zeroed=False,
        volume_acft=own_volume,
        load_tn_lb=own_tn,
        load_tp_lb=own_tp,
    )

    # Output flow = upstream components + this catchment's own component
    output_flow = Flow(components=list(upstream_flow.components) + [own_component])

    trace = TraceEntry(
        node_id=node['id'],
        node_type='catchment',
        node_name=node.get('name', ''),
        node_notes=node.get('notes', ''),
        inputs_summary={
            'volume_acft': upstream_flow.total_volume(),
            'tn_lb': upstream_flow.total_tn(),
            'tp_lb': upstream_flow.total_tp(),
            'components': [c.source_catchment_id for c in upstream_flow.components],
        },
        outputs_summary={
            'volume_acft': output_flow.total_volume(),
            'tn_lb': output_flow.total_tn(),
            'tp_lb': output_flow.total_tp(),
            'components': [c.source_catchment_id for c in output_flow.components],
        },
        calculation_details={
            'subareas': subarea_traces,
            'own_total_area_ac': own_total_area,
            'own_composite_cn': composite_cn,
            'own_composite_dcia': composite_dcia,
            'own_volume_acft': own_volume,
            'own_tn_lb': own_tn,
            'own_tp_lb': own_tp,
            'rainfall_in': rainfall,
            'zone': zone,
        },
        warnings=warnings,
    )

    return output_flow, trace


# ---------------------------------------------------------------------
# Dry retention BMP
# ---------------------------------------------------------------------

def process_dry_retention(node, inflow, project_meta):
    """Compute discharge of a dry retention BMP.

    The retention efficiency is looked up in Appendix O at the
    BMP's effective retention depth (storage / total contributing
    area) using the COMPOSITE CN and COMPOSITE DCIA across all
    upstream stream components — with DCIA zeroed for any component
    that already passed through an upstream dry retention BMP
    (the D-prime rule).

    The lookup gives a single efficiency that applies uniformly
    to all incoming load. After processing:
      - Volume out = volume_in × (1 - efficiency)
      - Load out (per stream component) = load_in × (1 - efficiency)
      - All downstream components have their dcia_zeroed flag set
        to True (since they've now passed through dry retention).

    Note: the DCIA-zeroing is applied to stream components for
    the purposes of FUTURE downstream dry retention lookups. It
    doesn't change anything about the current efficiency calculation.
    """
    zone = project_meta['zone']
    storage = node['storage_volume_acft']
    contributing_area = inflow.total_area()
    warnings = []

    if contributing_area == 0:
        # No upstream catchments — pond captures nothing
        warnings.append(
            f"Dry retention {node['id']} has no upstream contributing area; "
            f"efficiency calculation skipped."
        )
        return inflow, TraceEntry(
            node_id=node['id'],
            node_type='dry_retention',
            node_name=node.get('name', ''),
            node_notes=node.get('notes', ''),
            inputs_summary={'volume_acft': 0, 'tn_lb': 0, 'tp_lb': 0, 'components': []},
            outputs_summary={'volume_acft': 0, 'tn_lb': 0, 'tp_lb': 0, 'components': []},
            calculation_details={'storage_acft': storage, 'efficiency_pct': 0,
                                  'no_upstream': True},
            warnings=warnings,
        )

    # Compute retention depth in inches over contributing area
    # depth (in) = storage (ac-ft) × 12 (in/ft) / area (ac)
    retention_depth_in = storage * 12.0 / contributing_area
    composite_cn = inflow.composite_cn()
    composite_dcia = inflow.composite_dcia()

    eff_pct, lookup_warnings = tables.lookup_dry_retention_efficiency(
        zone, composite_cn, composite_dcia, retention_depth_in
    )
    warnings.extend(lookup_warnings)

    eff_frac = eff_pct / 100.0

    # Apply efficiency uniformly to each stream component, and mark
    # the DCIA as zeroed for downstream purposes.
    new_components = []
    for c in inflow.components:
        new_components.append(StreamComponent(
            source_catchment_id=c.source_catchment_id,
            area_ac=c.area_ac,
            composite_cn=c.composite_cn,
            composite_dcia=0.0,            # D-prime: zero out for downstream lookups
            dcia_zeroed=True,
            volume_acft=c.volume_acft * (1 - eff_frac),
            load_tn_lb=c.load_tn_lb * (1 - eff_frac),
            load_tp_lb=c.load_tp_lb * (1 - eff_frac),
        ))
    output_flow = Flow(components=new_components)

    trace = TraceEntry(
        node_id=node['id'],
        node_type='dry_retention',
        node_name=node.get('name', ''),
        node_notes=node.get('notes', ''),
        inputs_summary={
            'volume_acft': inflow.total_volume(),
            'tn_lb': inflow.total_tn(),
            'tp_lb': inflow.total_tp(),
            'components': [c.source_catchment_id for c in inflow.components],
        },
        outputs_summary={
            'volume_acft': output_flow.total_volume(),
            'tn_lb': output_flow.total_tn(),
            'tp_lb': output_flow.total_tp(),
            'components': [c.source_catchment_id for c in output_flow.components],
        },
        calculation_details={
            'storage_acft': storage,
            'contributing_area_ac': contributing_area,
            'retention_depth_in': retention_depth_in,
            'composite_cn': composite_cn,
            'composite_dcia': composite_dcia,
            'efficiency_pct': eff_pct,
            'volume_captured_acft': inflow.total_volume() * eff_frac,
            'tn_captured_lb': inflow.total_tn() * eff_frac,
            'tp_captured_lb': inflow.total_tp() * eff_frac,
            'method': 'Appendix O bilinear (CN, DCIA) + linear (depth) interpolation',
        },
        warnings=warnings,
    )
    return output_flow, trace


# ---------------------------------------------------------------------
# Wet detention BMP
# ---------------------------------------------------------------------

def process_wet_detention(node, inflow, project_meta, upstream_was_wet=False):
    """Compute discharge of a wet detention pond.

    Calculation steps:
      1. Compute residence time td = (permanent_pool / annual_inflow) × 365
      2. Apply base TN and TP curves (separate equations)
      3. Compound any user-enabled credits (littoral zone, floating
         islands at ≥5% coverage, alum injection) via Eq. 9-5
      4. Apply combined efficiency to load; volume passes through.

    Wet detention BMPs DO NOT change annual volume — inflow ≈ outflow
    on an annual basis. Load is reduced by the compound efficiency.

    The `upstream_was_wet` flag is set by the engine when a previous
    wet detention pond directly feeds this one with no other input
    between them, and triggers the td-summation warning per AH
    methodology PDF.
    """
    pool = node['permanent_pool_acft']
    inflow_volume = inflow.total_volume()
    warnings = []

    if inflow_volume == 0:
        # No inflow — no calculation possible
        warnings.append(
            f"Wet detention {node['id']} has no upstream volume; "
            f"residence time calculation skipped."
        )
        return inflow, TraceEntry(
            node_id=node['id'],
            node_type='wet_detention',
            node_name=node.get('name', ''),
            node_notes=node.get('notes', ''),
            inputs_summary={'volume_acft': 0, 'tn_lb': 0, 'tp_lb': 0, 'components': []},
            outputs_summary={'volume_acft': 0, 'tn_lb': 0, 'tp_lb': 0, 'components': []},
            calculation_details={'permanent_pool_acft': pool, 'no_upstream': True},
            warnings=warnings,
        )

    # td (days) = pool / annual_inflow × 365
    # AH §9.5 / detention methodology PDF
    td_days = pool / inflow_volume * 365.0

    # Base efficiencies from Harper & Baker (2007) curves
    # TN: monotonically saturating, plateaus around 43.75% as td → ∞
    # TP: log-quadratic; can exceed 80% at high td
    base_tn_pct = 43.75 * td_days / (4.38 + td_days)
    if td_days > 0:
        base_tp_pct = 40.13 + 6.372 * math.log(td_days) + 0.213 * (math.log(td_days) ** 2)
    else:
        base_tp_pct = 0.0
    # Clamp efficiencies to [0, 100] in case the formula goes out of range
    # (shouldn't happen with realistic td values, but defensive)
    base_tn_pct = max(0.0, min(100.0, base_tn_pct))
    base_tp_pct = max(0.0, min(100.0, base_tp_pct))

    # Floating islands credit
    floating_coverage = node.get('floating_islands_pct_coverage', 0.0)
    floating_override = node.get('floating_islands_credit_override', None)
    if floating_override is not None:
        floating_credit_pct = floating_override
        floating_source = f"User override: {floating_override}%"
    elif floating_coverage >= 5.0:
        floating_credit_pct = 12.0
        floating_source = (f"Auto-applied 12% credit at {floating_coverage}% pond coverage "
                            f"(≥5% threshold per Appendix O / Wanielista & Chang 2012)")
    else:
        floating_credit_pct = 0.0
        floating_source = (f"No credit applied; pond coverage {floating_coverage}% "
                            f"is below 5% threshold")

    littoral_credit_pct = node.get('littoral_zone_pct', 0.0)
    alum_tn_pct = node.get('alum_injection_credit_tn_pct', 0.0)
    alum_tp_pct = node.get('alum_injection_credit_tp_pct', 0.0)

    # Eq. 9-5 compounding: 1 - Π(1 - e_i)
    # TN gets: base TN × littoral × floating × alum_TN
    # TP gets: base TP × littoral × floating × alum_TP
    def compound(eff_pcts):
        prod = 1.0
        for e in eff_pcts:
            prod *= (1.0 - e / 100.0)
        return (1.0 - prod) * 100.0

    eff_tn_pct = compound([base_tn_pct, littoral_credit_pct, floating_credit_pct, alum_tn_pct])
    eff_tp_pct = compound([base_tp_pct, littoral_credit_pct, floating_credit_pct, alum_tp_pct])

    # Warnings
    if td_days > 200:
        warnings.append(
            f"Residence time {td_days:.1f} days exceeds typical design range "
            f"(>200 days). Verify pond sizing — efficiency curves are based on "
            f"data largely below this range."
        )
    if upstream_was_wet:
        warnings.append(
            f"Wet detention pond {node['id']} is directly downstream of another "
            f"wet detention pond with no other input between them. The FDEP "
            f"detention methodology recommends summing the residence times of "
            f"such ponds rather than compounding efficiencies via Eq. 9-5. "
            f"This calculation treats the ponds as independent. To use the "
            f"td-summation approach, combine the two ponds into one with the "
            f"summed permanent pool volume."
        )
    if littoral_credit_pct > 10.0:
        warnings.append(
            f"Littoral zone credit {littoral_credit_pct}% exceeds the 10% "
            f"maximum suggested by AH Vol II. Verify justification in notes."
        )

    # Apply compound efficiency uniformly to each stream component.
    # Wet detention does NOT zero out DCIA — captured pollutant in a
    # wet pond didn't preferentially capture small first-flush events,
    # so DCIA characteristics of upstream catchments remain valid for
    # any further downstream dry retention BMPs.
    eff_tn_frac = eff_tn_pct / 100.0
    eff_tp_frac = eff_tp_pct / 100.0
    new_components = []
    for c in inflow.components:
        new_components.append(StreamComponent(
            source_catchment_id=c.source_catchment_id,
            area_ac=c.area_ac,
            composite_cn=c.composite_cn,
            composite_dcia=c.composite_dcia,
            dcia_zeroed=c.dcia_zeroed,
            volume_acft=c.volume_acft,                          # volume unchanged
            load_tn_lb=c.load_tn_lb * (1 - eff_tn_frac),
            load_tp_lb=c.load_tp_lb * (1 - eff_tp_frac),
        ))
    output_flow = Flow(components=new_components)

    trace = TraceEntry(
        node_id=node['id'],
        node_type='wet_detention',
        node_name=node.get('name', ''),
        node_notes=node.get('notes', ''),
        inputs_summary={
            'volume_acft': inflow.total_volume(),
            'tn_lb': inflow.total_tn(),
            'tp_lb': inflow.total_tp(),
            'components': [c.source_catchment_id for c in inflow.components],
        },
        outputs_summary={
            'volume_acft': output_flow.total_volume(),
            'tn_lb': output_flow.total_tn(),
            'tp_lb': output_flow.total_tp(),
            'components': [c.source_catchment_id for c in output_flow.components],
        },
        calculation_details={
            'permanent_pool_acft': pool,
            'inflow_volume_acft_yr': inflow_volume,
            'residence_time_days': td_days,
            'base_tn_pct': base_tn_pct,
            'base_tp_pct': base_tp_pct,
            'littoral_credit_pct': littoral_credit_pct,
            'littoral_notes': node.get('littoral_zone_notes', ''),
            'floating_islands_pct_coverage': floating_coverage,
            'floating_islands_credit_pct': floating_credit_pct,
            'floating_islands_source': floating_source,
            'floating_islands_notes': node.get('floating_islands_notes', ''),
            'alum_tn_pct': alum_tn_pct,
            'alum_tp_pct': alum_tp_pct,
            'alum_notes': node.get('alum_injection_notes', ''),
            'eff_tn_pct': eff_tn_pct,
            'eff_tp_pct': eff_tp_pct,
            'tn_captured_lb': inflow.total_tn() * eff_tn_frac,
            'tp_captured_lb': inflow.total_tp() * eff_tp_frac,
            'method': ('td = pool/inflow × 365; '
                       'base TN = 43.75·td/(4.38+td); '
                       'base TP = 40.13 + 6.372·ln(td) + 0.213·(ln td)²; '
                       'credits compounded via Eq. 9-5'),
        },
        warnings=warnings,
    )
    return output_flow, trace


# ---------------------------------------------------------------------
# Other BMP (flat-percentage)
# ---------------------------------------------------------------------

# Preset defaults — for reference and report citations only.
# The actual values used in calculation come from the JSON's
# tn_removal_pct and tp_removal_pct fields, which are populated
# by the UI based on the preset but always editable.
OTHER_BMP_PRESETS = {
    'baffle_box_1st_gen': {
        'name': 'Baffle Box (1st generation)',
        'default_tn_pct': 0.50,
        'default_tp_pct': 2.30,
        'source': 'Appendix O Table 1; GPI 2010 (Contract S0236), '
                  'UCF for FDOT, Casselberry (Contract S0497)',
    },
    'baffle_box_2nd_gen': {
        'name': 'Baffle Box (2nd generation)',
        'default_tn_pct': 19.05,
        'default_tp_pct': 15.50,
        'source': 'Appendix O Table 1; GPI 2010 (Contract S0236), '
                  'UCF for FDOT, Casselberry (Contract S0497)',
    },
    'hydrodynamic_separator': {
        'name': 'Hydrodynamic Separator (CDS / Vortex)',
        'default_tn_pct': 0.0,
        'default_tp_pct': 10.0,
        'source': 'Appendix O Table 1; Sanford Stormceptor (Contract S0095, 2008), '
                  'Broadway Outfall (Contract WM793, 2006). Note: TN listed as N/A '
                  'in Appendix O; treated as 0% removal.',
    },
    'custom': {
        'name': 'Custom / Other',
        'default_tn_pct': 0.0,
        'default_tp_pct': 0.0,
        'source': 'User-specified; see notes field.',
    },
}


def process_other_bmp(node, inflow, project_meta):
    """Apply a flat-percentage removal to load. Volume passes through.

    These BMPs (baffle boxes, hydrodynamic separators, alum-only
    units, custom GSI elements, etc.) do not capture volume — they
    treat passing flow. Per Appendix O, they're characterized by
    constant TN and TP removal percentages.
    """
    preset = node.get('preset', 'custom')
    tn_pct = node.get('tn_removal_pct', 0.0)
    tp_pct = node.get('tp_removal_pct', 0.0)
    warnings = []

    preset_info = OTHER_BMP_PRESETS.get(preset, OTHER_BMP_PRESETS['custom'])

    if preset == 'custom' and not (node.get('notes') or '').strip():
        warnings.append(
            f"BMP {node['id']} uses preset='custom' but has no notes "
            f"explaining the source of the removal percentages. Add a "
            f"citation or justification to the notes field."
        )

    eff_tn_frac = tn_pct / 100.0
    eff_tp_frac = tp_pct / 100.0
    new_components = []
    for c in inflow.components:
        new_components.append(StreamComponent(
            source_catchment_id=c.source_catchment_id,
            area_ac=c.area_ac,
            composite_cn=c.composite_cn,
            composite_dcia=c.composite_dcia,
            dcia_zeroed=c.dcia_zeroed,
            volume_acft=c.volume_acft,
            load_tn_lb=c.load_tn_lb * (1 - eff_tn_frac),
            load_tp_lb=c.load_tp_lb * (1 - eff_tp_frac),
        ))
    output_flow = Flow(components=new_components)

    trace = TraceEntry(
        node_id=node['id'],
        node_type='other_bmp',
        node_name=node.get('name', ''),
        node_notes=node.get('notes', ''),
        inputs_summary={
            'volume_acft': inflow.total_volume(),
            'tn_lb': inflow.total_tn(),
            'tp_lb': inflow.total_tp(),
            'components': [c.source_catchment_id for c in inflow.components],
        },
        outputs_summary={
            'volume_acft': output_flow.total_volume(),
            'tn_lb': output_flow.total_tn(),
            'tp_lb': output_flow.total_tp(),
            'components': [c.source_catchment_id for c in output_flow.components],
        },
        calculation_details={
            'preset': preset,
            'preset_name': preset_info['name'],
            'preset_default_tn_pct': preset_info['default_tn_pct'],
            'preset_default_tp_pct': preset_info['default_tp_pct'],
            'tn_removal_pct': tn_pct,
            'tp_removal_pct': tp_pct,
            'source': preset_info['source'],
            'tn_captured_lb': inflow.total_tn() * eff_tn_frac,
            'tp_captured_lb': inflow.total_tp() * eff_tp_frac,
        },
        warnings=warnings,
    )
    return output_flow, trace


# ---------------------------------------------------------------------
# Outfall (terminal node, just records the values)
# ---------------------------------------------------------------------

def process_outfall(node, inflow, project_meta):
    """Outfall is a sink. Just records what arrived."""
    trace = TraceEntry(
        node_id=node['id'],
        node_type='outfall',
        node_name=node.get('name', ''),
        node_notes=node.get('notes', ''),
        inputs_summary={
            'volume_acft': inflow.total_volume(),
            'tn_lb': inflow.total_tn(),
            'tp_lb': inflow.total_tp(),
            'components': [c.source_catchment_id for c in inflow.components],
        },
        outputs_summary={
            'volume_acft': inflow.total_volume(),
            'tn_lb': inflow.total_tn(),
            'tp_lb': inflow.total_tp(),
            'components': [c.source_catchment_id for c in inflow.components],
        },
        calculation_details={'is_outfall': True},
        warnings=[],
    )
    return inflow, trace
