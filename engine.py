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
engine.py — Main calculation engine for FDEP BMP routing.

This is the entry point. Loads a project JSON file, validates the
schema, walks the node graph in topological order applying each
node's calculation, accumulates a trace of every step, and emits
both a console summary and an HTML audit report.

Usage:
    python engine.py <project.json>

The HTML report is written next to the input file with a `_report.html`
suffix.

The engine pipeline:
  1. parse_project()    — read and validate JSON
  2. build_graph()      — extract topology, check for errors
  3. topological_sort() — order nodes for correct evaluation
  4. run_calculations() — walk the graph, applying each node's BMP class
  5. summarize()        — console output
  6. write_html_report()— audit document

Validation errors are reported with line/field references where
possible. Warnings (vs. errors) are accumulated and shown in both
the console output and the HTML report; they don't block calculation.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

import bmps
import tables
import report


SUPPORTED_SCHEMA_VERSIONS = {"1.0"}


# ---------------------------------------------------------------------
# Public API for server use
# ---------------------------------------------------------------------

def calculate(project):
    """Validate and calculate. Returns the result dict.

    Used by both the CLI (engine.main) and the HTTP server (server.py).
    Doesn't write any files; returns calculations in memory.

    Raises ValidationError if project schema/structure is invalid.
    """
    # Schema version check (mirrors what parse_project does for files)
    schema = project.get('schema_version')
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationError(
            f"schema_version '{schema}' is not supported. "
            f"This engine supports: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    # Top-level required keys
    for key in ('project', 'nodes'):
        if key not in project:
            raise ValidationError(f"Project missing required key: '{key}'")
    meta = project['project']
    for key in ('name', 'zone', 'annual_rainfall_in'):
        if key not in meta:
            raise ValidationError(f"project.{key} is required")
    if meta['zone'] not in (1, 2, 3, 4, 5):
        raise ValidationError(f"project.zone must be 1-5, got {meta['zone']}")
    if meta['annual_rainfall_in'] <= 0:
        raise ValidationError("project.annual_rainfall_in must be > 0")
    validate_nodes(project)
    return run_calculations(project)


def result_to_serializable(result):
    """Convert engine result (with dataclasses) to plain dicts for JSON.

    The internal result has TraceEntry objects and Flow objects with
    StreamComponent dataclasses. These need to be flattened into plain
    dicts before JSON serialization for the HTTP API.
    """
    return {
        'baseline_volume_acft': result['baseline_volume_acft'],
        'baseline_tn_lb': result['baseline_tn_lb'],
        'baseline_tp_lb': result['baseline_tp_lb'],
        'warnings': result['warnings'],
        'outfall_results': result['outfall_results'],
        'traces': [
            {
                'node_id': t.node_id,
                'node_type': t.node_type,
                'node_name': t.node_name,
                'node_notes': t.node_notes,
                'inputs_summary': t.inputs_summary,
                'outputs_summary': t.outputs_summary,
                'calculation_details': t.calculation_details,
                'warnings': t.warnings,
            }
            for t in result['traces']
        ],
    }


# ---------------------------------------------------------------------
# Project file loading and schema validation
# ---------------------------------------------------------------------

class ValidationError(Exception):
    """Raised when the project file fails schema validation."""
    pass


def parse_project(path):
    """Load and parse a project JSON file. Returns the dict.

    Raises ValidationError with a clear message if the file is malformed
    or missing required fields.
    """
    with open(path, encoding='utf-8-sig') as f:
        try:
            project = json.load(f)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON in {path}: {e}")

    schema = project.get('schema_version')
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationError(
            f"schema_version '{schema}' is not supported. "
            f"This engine supports: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    # Top-level required keys
    for key in ('project', 'nodes'):
        if key not in project:
            raise ValidationError(f"Project file missing required key: '{key}'")

    # Project metadata required keys
    meta = project['project']
    for key in ('name', 'zone', 'annual_rainfall_in'):
        if key not in meta:
            raise ValidationError(f"project.{key} is required")
    if meta['zone'] not in (1, 2, 3, 4, 5):
        raise ValidationError(f"project.zone must be 1-5, got {meta['zone']}")
    if meta['annual_rainfall_in'] <= 0:
        raise ValidationError(f"project.annual_rainfall_in must be > 0")

    return project


def validate_nodes(project):
    """Check every node for required fields and field types.

    Catches problems early so that the engine doesn't fail with
    unhelpful errors deep in the calculation pipeline.
    """
    nodes = project['nodes']
    if not nodes:
        raise ValidationError("Project has no nodes.")

    seen_ids = set()
    valid_types = {'catchment', 'dry_retention', 'wet_detention', 'other_bmp', 'outfall'}

    for n in nodes:
        if 'id' not in n:
            raise ValidationError(f"Node missing 'id' field: {n}")
        if n['id'] in seen_ids:
            raise ValidationError(f"Duplicate node id: '{n['id']}'")
        seen_ids.add(n['id'])
        if 'type' not in n:
            raise ValidationError(f"Node {n['id']} missing 'type' field")
        if n['type'] not in valid_types:
            raise ValidationError(
                f"Node {n['id']} has invalid type '{n['type']}'. "
                f"Valid types: {sorted(valid_types)}"
            )

    # Type-specific validation
    for n in nodes:
        nid = n['id']
        if n['type'] == 'catchment':
            if 'subareas' not in n or not n['subareas']:
                raise ValidationError(f"Catchment {nid} must have at least one subarea.")
            for i, sub in enumerate(n['subareas']):
                for f in ('land_cover', 'area_ac', 'non_dcia_cn', 'dcia_pct'):
                    if f not in sub:
                        raise ValidationError(
                            f"Catchment {nid} subarea {i} missing '{f}'"
                        )
                if sub['area_ac'] <= 0:
                    raise ValidationError(
                        f"Catchment {nid} subarea {i} area_ac must be > 0"
                    )
                if not (0 <= sub['dcia_pct'] <= 100):
                    raise ValidationError(
                        f"Catchment {nid} subarea {i} dcia_pct must be 0-100"
                    )
                if not (30 <= sub['non_dcia_cn'] <= 98):
                    raise ValidationError(
                        f"Catchment {nid} subarea {i} non_dcia_cn must be 30-98"
                    )
        elif n['type'] == 'dry_retention':
            if 'storage_volume_acft' not in n or n['storage_volume_acft'] <= 0:
                raise ValidationError(
                    f"Dry retention {nid} must have storage_volume_acft > 0"
                )
        elif n['type'] == 'wet_detention':
            if 'permanent_pool_acft' not in n or n['permanent_pool_acft'] <= 0:
                raise ValidationError(
                    f"Wet detention {nid} must have permanent_pool_acft > 0"
                )
        elif n['type'] == 'other_bmp':
            for f in ('preset', 'tn_removal_pct', 'tp_removal_pct'):
                if f not in n:
                    raise ValidationError(f"Other BMP {nid} missing '{f}'")

        # Validate drains_to references
        drains_to = n.get('drains_to')
        if n['type'] == 'outfall':
            if drains_to is not None:
                raise ValidationError(
                    f"Outfall {nid} must not have a drains_to (got '{drains_to}')."
                )
        else:
            if drains_to is not None and drains_to not in seen_ids:
                raise ValidationError(
                    f"Node {nid} drains_to references unknown node '{drains_to}'"
                )


# ---------------------------------------------------------------------
# Graph topology
# ---------------------------------------------------------------------

def build_graph(project):
    """Build adjacency information from the node list.

    Returns:
      nodes_by_id: dict {id: node_dict}
      upstream:    dict {id: list of upstream node ids that drain to this one}
      sources:     list of catchment node ids with no upstream input
      outfalls:    list of outfall node ids
    """
    nodes_by_id = {n['id']: n for n in project['nodes']}
    upstream = {n['id']: [] for n in project['nodes']}
    for n in project['nodes']:
        dt = n.get('drains_to')
        if dt is not None:
            upstream[dt].append(n['id'])

    sources = []
    outfalls = []
    for n in project['nodes']:
        if n['type'] == 'outfall':
            outfalls.append(n['id'])
        if n['type'] == 'catchment' and not upstream[n['id']]:
            sources.append(n['id'])

    if not outfalls:
        raise ValidationError("Project has no outfalls.")

    return nodes_by_id, upstream, sources, outfalls


def topological_sort(nodes_by_id, upstream):
    """Order nodes so that every node's upstream predecessors come first.

    Uses Kahn's algorithm. Detects cycles and raises ValidationError
    pointing to the cycle.
    """
    # in_degree = number of incoming edges (= len of upstream[id])
    in_degree = {nid: len(ups) for nid, ups in upstream.items()}
    ordered = []
    ready = [nid for nid, d in in_degree.items() if d == 0]
    # We process in deterministic order for reproducibility
    ready.sort()

    # Build reverse: for each node, who does it drain to?
    drains_to = {nid: nodes_by_id[nid].get('drains_to') for nid in nodes_by_id}

    while ready:
        nid = ready.pop(0)
        ordered.append(nid)
        target = drains_to[nid]
        if target is not None:
            in_degree[target] -= 1
            if in_degree[target] == 0:
                # Insert in sorted position to keep order deterministic
                # (small graphs; O(n) insert is fine)
                pos = 0
                while pos < len(ready) and ready[pos] < target:
                    pos += 1
                ready.insert(pos, target)

    if len(ordered) != len(nodes_by_id):
        unprocessed = sorted(set(nodes_by_id) - set(ordered))
        raise ValidationError(
            f"Cycle detected in graph. Unprocessed nodes: {unprocessed}. "
            f"Check drains_to fields for circular references."
        )
    return ordered


def find_unreachable_catchments(nodes_by_id, ordered):
    """Find catchments whose paths terminate in null without reaching an outfall.

    Returns a list of catchment IDs that don't reach any outfall.
    These are warnings, not errors — the user might be staging changes.
    """
    # Walk from each catchment via drains_to and check terminus
    unreachable = []
    for nid, node in nodes_by_id.items():
        if node['type'] != 'catchment':
            continue
        cur = nid
        seen = set()
        while cur is not None:
            if cur in seen:
                break  # cycle, but topo_sort would have caught it
            seen.add(cur)
            n = nodes_by_id[cur]
            if n['type'] == 'outfall':
                break
            cur = n.get('drains_to')
        else:
            # Loop completed because cur became None
            pass
        # If we didn't reach an outfall, mark as unreachable
        if cur is None or nodes_by_id[cur]['type'] != 'outfall':
            unreachable.append(nid)
    return unreachable


# ---------------------------------------------------------------------
# Calculation walk
# ---------------------------------------------------------------------

def run_calculations(project):
    """Walk the graph and apply each node's calculation.

    Returns a result dict containing:
      - traces: list of TraceEntry objects in evaluation order
      - outfall_results: dict {outfall_id: {volume, tn, tp}}
      - flows_by_node: dict {node_id: Flow at node's output}
      - warnings: list of warning strings (project-level)
      - baseline: total catchment-generated load (before any treatment)
    """
    nodes_by_id, upstream, sources, outfalls = build_graph(project)
    ordered = topological_sort(nodes_by_id, upstream)
    project_meta = project['project']

    # Detect unreachable catchments (warning, not error)
    project_warnings = []
    unreachable = find_unreachable_catchments(nodes_by_id, ordered)
    for u in unreachable:
        project_warnings.append(
            f"Catchment '{u}' does not reach any outfall (drains_to chain "
            f"terminates in null or non-outfall). Its load will not contribute "
            f"to any outfall total."
        )

    # Walk the graph
    flows_by_node = {}      # node_id -> output Flow
    traces = []             # list of TraceEntry in evaluation order

    for nid in ordered:
        node = nodes_by_id[nid]
        # Combine flows from all upstream predecessors that drain to this node
        upstream_components = []
        for up_id in upstream[nid]:
            upstream_components.extend(flows_by_node[up_id].components)
        upstream_flow = bmps.Flow(components=upstream_components)

        # Detect "wet pond directly downstream of wet pond" for warnings
        upstream_was_wet = (
            node['type'] == 'wet_detention'
            and len(upstream[nid]) == 1
            and nodes_by_id[upstream[nid][0]]['type'] == 'wet_detention'
        )

        if node['type'] == 'catchment':
            output, trace = bmps.process_catchment(node, upstream_flow, project_meta)
        elif node['type'] == 'dry_retention':
            output, trace = bmps.process_dry_retention(node, upstream_flow, project_meta)
        elif node['type'] == 'wet_detention':
            output, trace = bmps.process_wet_detention(
                node, upstream_flow, project_meta, upstream_was_wet=upstream_was_wet
            )
        elif node['type'] == 'other_bmp':
            output, trace = bmps.process_other_bmp(node, upstream_flow, project_meta)
        elif node['type'] == 'outfall':
            output, trace = bmps.process_outfall(node, upstream_flow, project_meta)
        else:
            raise ValidationError(f"Unknown node type: {node['type']}")

        flows_by_node[nid] = output
        traces.append(trace)

    # Per-outfall results
    outfall_results = {}
    for oid in outfalls:
        f = flows_by_node[oid]
        outfall_results[oid] = {
            'name': nodes_by_id[oid].get('name', ''),
            'notes': nodes_by_id[oid].get('notes', ''),
            'volume_acft': f.total_volume(),
            'tn_lb': f.total_tn(),
            'tp_lb': f.total_tp(),
        }

    # Baseline: total load generated by all catchments before any treatment
    baseline_volume = 0.0
    baseline_tn = 0.0
    baseline_tp = 0.0
    for n in project['nodes']:
        if n['type'] != 'catchment':
            continue
        # Get this catchment's "own" generation from its trace
        # (trace was added in the same order as the ordered walk)
        for t in traces:
            if t.node_id == n['id']:
                d = t.calculation_details
                baseline_volume += d.get('own_volume_acft', 0)
                baseline_tn += d.get('own_tn_lb', 0)
                baseline_tp += d.get('own_tp_lb', 0)
                break

    return {
        'traces': traces,
        'outfall_results': outfall_results,
        'flows_by_node': flows_by_node,
        'warnings': project_warnings,
        'baseline_volume_acft': baseline_volume,
        'baseline_tn_lb': baseline_tn,
        'baseline_tp_lb': baseline_tp,
        'nodes_by_id': nodes_by_id,
    }


# ---------------------------------------------------------------------
# Conversion utilities
# ---------------------------------------------------------------------

LB_PER_KG = 2.20462


def lb_to_kg(lb):
    return lb / LB_PER_KG


def fmt_load(lb_value):
    """Format a load value showing both lb/yr and kg/yr."""
    return f"{lb_value:.2f} lb/yr ({lb_to_kg(lb_value):.2f} kg/yr)"


# ---------------------------------------------------------------------
# Console summary output
# ---------------------------------------------------------------------

def summarize(result, project):
    """Print a console summary of results."""
    meta = project['project']
    print()
    print("=" * 70)
    print(f"BMP Routing Tool — Results")
    print("=" * 70)
    print(f"Project:         {meta['name']}")
    print(f"Zone:            {meta['zone']}")
    print(f"Annual rainfall: {meta['annual_rainfall_in']} in")
    print()
    print("BASELINE (untreated catchment-generated load):")
    print(f"  Volume:  {result['baseline_volume_acft']:.3f} ac-ft/yr")
    print(f"  TN:      {fmt_load(result['baseline_tn_lb'])}")
    print(f"  TP:      {fmt_load(result['baseline_tp_lb'])}")
    print()
    print("OUTFALLS:")
    total_v = total_tn = total_tp = 0.0
    for oid, r in result['outfall_results'].items():
        print(f"  {oid} — {r['name']}:")
        print(f"    Volume:  {r['volume_acft']:.3f} ac-ft/yr")
        print(f"    TN:      {fmt_load(r['tn_lb'])}")
        print(f"    TP:      {fmt_load(r['tp_lb'])}")
        total_v += r['volume_acft']
        total_tn += r['tn_lb']
        total_tp += r['tp_lb']
    print()
    print("PROJECT TOTAL (all outfalls):")
    print(f"  Volume:  {total_v:.3f} ac-ft/yr")
    print(f"  TN:      {fmt_load(total_tn)}")
    print(f"  TP:      {fmt_load(total_tp)}")
    print()

    # Reductions
    if result['baseline_tn_lb'] > 0:
        tn_red = (1 - total_tn / result['baseline_tn_lb']) * 100
    else:
        tn_red = 0
    if result['baseline_tp_lb'] > 0:
        tp_red = (1 - total_tp / result['baseline_tp_lb']) * 100
    else:
        tp_red = 0
    print("REDUCTIONS (from baseline to outfalls):")
    print(f"  TN reduction: {tn_red:.2f}%")
    print(f"  TP reduction: {tp_red:.2f}%")
    print()

    # Warnings
    all_warnings = list(result['warnings'])
    for t in result['traces']:
        for w in t.warnings:
            all_warnings.append(f"[{t.node_id}] {w}")
    if all_warnings:
        print("WARNINGS:")
        for w in all_warnings:
            print(f"  ⚠ {w}")
    else:
        print("No warnings.")
    print()


# ---------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------

def main(argv=None):
    from _version import __version__
    parser = argparse.ArgumentParser(
        description="SAGE — Stormwater Analysis & GSI Evaluator. "
                    "FDEP BMP routing calculation engine."
    )
    parser.add_argument("--version", action="version",
                        version=f"SAGE {__version__}")
    parser.add_argument("project_file", help="Path to project JSON file.")
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip writing the HTML report (console output only).",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Generate one-page summary report instead of full audit report.",
    )
    parser.add_argument(
        "--both", action="store_true",
        help="Generate both summary and full report.",
    )
    args = parser.parse_args(argv)

    project_path = Path(args.project_file)
    if not project_path.exists():
        print(f"Error: project file not found: {project_path}", file=sys.stderr)
        return 1

    try:
        project = parse_project(project_path)
        validate_nodes(project)
    except ValidationError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        return 2

    # Update date_modified (informational)
    project['project']['date_modified'] = datetime.date.today().isoformat()

    result = run_calculations(project)
    summarize(result, project)

    if not args.no_report:
        if args.both or not args.summary:
            full_path = project_path.with_name(project_path.stem + "_report.html")
            report.write_html_report(full_path, project, result, style='full')
            print(f"Full HTML report written to: {full_path}")
        if args.summary or args.both:
            summary_path = project_path.with_name(project_path.stem + "_summary.html")
            report.write_html_report(summary_path, project, result, style='summary')
            print(f"Summary HTML report written to: {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
