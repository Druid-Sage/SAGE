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
report.py — HTML audit report generator.

Generates a printable HTML document showing every calculation step
with citations to the FDEP AH Vol I sections, equations, and
appendices used. The report's purpose is to make every number
in the engine's output traceable, so a reviewer can verify the
calculations against the source rule.

Design goals:
  - Self-contained: single HTML file, no external CSS or JS
  - Printable: works as PDF when printed from a browser
  - Read top-to-bottom: project header → per-node breakdown →
    final summary
  - Every calculation cites its source equation/table
"""

import datetime
import html

from _version import __version__


def lb_to_kg(lb):
    return lb / 2.20462


def fmt_load_html(lb_value):
    """Format load showing lb/yr and kg/yr for the report."""
    return (f"<b>{lb_value:.2f}</b> lb/yr "
            f"<span class='alt-units'>({lb_to_kg(lb_value):.2f} kg/yr)</span>")


def fmt_volume_html(v):
    return f"<b>{v:.3f}</b> ac-ft/yr"


# ---------------------------------------------------------------------
# CSS styling — embedded for self-containment
# ---------------------------------------------------------------------

CSS = """
:root {
  /* Druid brand palette — see druid.solutions */
  --druid-forest:   #0F3F37;
  --druid-sage:     #7FB9A3;
  --druid-gold:     #D9B85F;
  --druid-charcoal: #101416;
  --druid-paper:    #F4F1E8;
  --druid-paper-2:  #EEF5F1;
  --druid-rule:     #d6d2c4;
}
body {
  font-family: 'Source Sans 3', 'Source Sans Pro', 'Helvetica Neue', Arial, sans-serif;
  max-width: 900px;
  margin: 2em auto;
  padding: 0 1em;
  color: var(--druid-charcoal);
  background: var(--druid-paper);
  line-height: 1.5;
}
h1 {
  border-bottom: 3px solid var(--druid-gold);
  padding-bottom: .3em;
  color: var(--druid-forest);
  font-weight: 700;
}
h2 {
  color: var(--druid-forest);
  margin-top: 2em;
  border-bottom: 1px solid var(--druid-rule);
  padding-bottom: .2em;
  font-weight: 600;
}
h3 {
  color: var(--druid-charcoal);
  margin-top: 1.5em;
  font-weight: 600;
}
h4 {
  color: var(--druid-forest);
  font-weight: 600;
  margin-top: 1em;
}
.meta-table, .calc-table, .summary-table {
  border-collapse: collapse;
  margin: .5em 0;
  width: 100%;
}
.meta-table td, .meta-table th,
.calc-table td, .calc-table th,
.summary-table td, .summary-table th {
  border: 1px solid var(--druid-rule);
  padding: .35em .7em;
  text-align: left;
  vertical-align: top;
}
.meta-table th, .calc-table th, .summary-table th {
  background: var(--druid-paper-2);
  font-weight: 600;
  color: var(--druid-forest);
}
.summary-table td.num, .calc-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.summary-table th.num, .calc-table th.num { text-align: right; }
.alt-units { color: #666; font-size: .92em; }
.warning {
  background: #fff5e0;
  border-left: 4px solid var(--druid-gold);
  padding: .5em .8em;
  margin: .5em 0;
}
.notes {
  background: var(--druid-paper-2);
  border-left: 4px solid var(--druid-sage);
  padding: .4em .7em;
  margin: .3em 0;
  font-style: italic;
  color: #444;
}
.equation {
  font-family: 'Source Serif 4', 'Times New Roman', serif;
  font-style: italic;
  margin: .3em 0 .3em 2em;
  font-size: 1.05em;
  color: var(--druid-charcoal);
}
.citation { color: var(--druid-forest); font-size: .92em; }
.node-section {
  margin: 1em 0;
  padding: 1em;
  border: 1px solid var(--druid-rule);
  border-radius: 4px;
  background: #fff;
}
.node-id {
  display: inline-block;
  background: var(--druid-forest);
  color: var(--druid-paper);
  padding: .15em .5em;
  border-radius: 3px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: .9em;
  margin-right: .5em;
}
.node-type {
  display: inline-block;
  background: #555;
  color: white;
  padding: .15em .5em;
  border-radius: 3px;
  font-size: .85em;
  margin-right: .5em;
}
.flow-summary {
  margin: .3em 0;
  padding: .3em .5em;
  background: var(--druid-paper-2);
  border-radius: 3px;
}
.subarea-table {
  margin: .5em 0 1em 1em;
  width: calc(100% - 1em);
  border-collapse: collapse;
}
.subarea-table td, .subarea-table th {
  border: 1px solid var(--druid-rule);
  padding: .25em .5em;
  font-size: .92em;
}
.subarea-table th { background: var(--druid-paper-2); }

/* Connectivity diagram */
.connectivity-diagram {
  margin: 1em 0;
  padding: 1em;
  background: #fff;
  border: 1px solid var(--druid-rule);
  border-radius: 4px;
  text-align: center;
}
.connectivity-diagram svg { max-width: 100%; height: auto; }

/* Brand mark in footer */
.brand-footer {
  margin-top: 3em;
  padding-top: 1em;
  border-top: 2px solid var(--druid-gold);
  text-align: center;
  color: #666;
  font-size: .85em;
}
.brand-footer .brand-name {
  color: var(--druid-forest);
  font-weight: 700;
  font-size: 1em;
  letter-spacing: .15em;
}
.brand-footer .tagline { font-style: italic; margin-top: .2em; }

/* Summary-mode styles: optimized for one-page printing */
body.summary {
  max-width: 7.5in;
  margin: 0 auto;
  padding: .4in .25in;
  font-size: 10.5pt;
}
body.summary h1 { font-size: 18pt; margin-top: 0; margin-bottom: .3em; }
body.summary h2 { font-size: 13pt; margin-top: .8em; margin-bottom: .3em; }
body.summary h3 { font-size: 11pt; margin-top: .5em; margin-bottom: .2em; }
body.summary .meta-table, body.summary .summary-table { font-size: 10pt; }
body.summary .connectivity-diagram { padding: .3em; margin: .5em 0; }

@media print {
  body { max-width: none; margin: 1em; }
  .node-section { page-break-inside: avoid; }
  body.summary { padding: .3in; margin: 0; max-width: none; }
}
"""


def esc(s):
    """HTML-escape, gracefully handling None and non-strings."""
    if s is None:
        return ""
    return html.escape(str(s))


# ---------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------

def render_header(project):
    meta = project['project']
    return f"""
    <h1>FDEP BMP Routing — Audit Report</h1>
    <table class='meta-table'>
      <tr><th>Project name</th><td>{esc(meta.get('name'))}</td></tr>
      <tr><th>Applicant</th><td>{esc(meta.get('applicant'))}</td></tr>
      <tr><th>Created</th><td>{esc(meta.get('date_created'))}</td></tr>
      <tr><th>Last modified</th><td>{esc(meta.get('date_modified'))}</td></tr>
      <tr><th>Meteorological zone</th><td>{esc(meta.get('zone'))} (per AH Appendix M)</td></tr>
      <tr><th>Annual rainfall</th><td>{esc(meta.get('annual_rainfall_in'))} in (per AH §9.4 / Appendix M)</td></tr>
      <tr><th>Default units</th><td>{esc(meta.get('default_units'))}</td></tr>
      <tr><th>Report generated</th><td>{datetime.datetime.now().isoformat(timespec='seconds')}</td></tr>
    </table>
    {render_notes_block(meta.get('notes'), 'Project notes')}
    """


def render_notes_block(text, label='Notes'):
    if not text or not text.strip():
        return ""
    return f"<div class='notes'><b>{esc(label)}:</b> {esc(text)}</div>"


def render_warnings_block(warnings):
    if not warnings:
        return ""
    items = "".join(f"<div class='warning'>⚠ {esc(w)}</div>" for w in warnings)
    return f"<h2>Warnings</h2>{items}"


def render_catchment_trace(trace):
    d = trace.calculation_details
    rows = []
    rows.append(f"<tr>"
                f"<th>Subarea</th>"
                f"<th>Land cover</th>"
                f"<th class='num'>Area (ac)</th>"
                f"<th class='num'>CN</th>"
                f"<th class='num'>%DCIA</th>"
                f"<th class='num'>ROC</th>"
                f"<th class='num'>EMC TN (mg/L)</th>"
                f"<th class='num'>EMC TP (mg/L)</th>"
                f"<th class='num'>Volume (ac-ft/yr)</th>"
                f"<th class='num'>TN (lb/yr)</th>"
                f"<th class='num'>TP (lb/yr)</th>"
                f"</tr>")
    for i, sub in enumerate(d['subareas'], 1):
        rows.append(f"<tr>"
                    f"<td>#{i}</td>"
                    f"<td>{esc(sub['land_cover'])}</td>"
                    f"<td class='num'>{sub['area_ac']:.2f}</td>"
                    f"<td class='num'>{sub['non_dcia_cn']:.2f}</td>"
                    f"<td class='num'>{sub['dcia_pct']:.2f}</td>"
                    f"<td class='num'>{sub['roc']:.4f}</td>"
                    f"<td class='num'>{sub['tn_emc_mgL']:.3f}</td>"
                    f"<td class='num'>{sub['tp_emc_mgL']:.3f}</td>"
                    f"<td class='num'>{sub['volume_acft']:.3f}</td>"
                    f"<td class='num'>{sub['load_tn_lb']:.2f}</td>"
                    f"<td class='num'>{sub['load_tp_lb']:.2f}</td>"
                    f"</tr>")

    upstream_section = ""
    if trace.inputs_summary['volume_acft'] > 0:
        upstream_section = (
            f"<div class='flow-summary'>"
            f"<b>Upstream input combined into this catchment:</b><br>"
            f"Volume: {fmt_volume_html(trace.inputs_summary['volume_acft'])}; "
            f"TN: {fmt_load_html(trace.inputs_summary['tn_lb'])}; "
            f"TP: {fmt_load_html(trace.inputs_summary['tp_lb'])}<br>"
            f"From upstream catchments: "
            f"{', '.join(esc(c) for c in trace.inputs_summary['components']) or '(none)'}"
            f"</div>"
        )

    subarea_notes = []
    for i, sub in enumerate(d['subareas'], 1):
        if sub.get('notes'):
            subarea_notes.append(f"Subarea #{i}: {sub['notes']}")
    notes_html = ""
    if subarea_notes:
        notes_html = "<div class='notes'><b>Subarea notes:</b><br>" + "<br>".join(esc(n) for n in subarea_notes) + "</div>"

    return f"""
    <div class='node-section'>
      <h3><span class='node-id'>{esc(trace.node_id)}</span>
          <span class='node-type'>catchment</span>
          {esc(trace.node_name)}</h3>
      {render_notes_block(trace.node_notes, 'Catchment notes')}
      <p>
        <span class='citation'>Calculation per AH §9.2.1 (Eq. 9-1: Annual Runoff Volume)
        and §9.2.2 (Eq. 9-2: Annual Mass Loading).</span>
      </p>
      <div class='equation'>Annual Runoff (ac-ft/yr) = Area (ac) × Annual Rainfall (in) × ROC × (1/12)</div>
      <div class='equation'>Annual Load (lb/yr) = Annual Runoff (ac-ft/yr) × EMC (mg/L) × 2.71947</div>
      <p>ROC values from AH Appendix N; EMC defaults from AH Table 9.2.</p>
      {upstream_section}
      <h4>Subarea breakdown</h4>
      <table class='subarea-table'>{''.join(rows)}</table>
      {notes_html}
      <p>
        <b>Catchment own generation:</b> Volume {fmt_volume_html(d['own_volume_acft'])};
        TN {fmt_load_html(d['own_tn_lb'])};
        TP {fmt_load_html(d['own_tp_lb'])}<br>
        <b>Composite CN:</b> {d['own_composite_cn']:.2f};
        <b>Composite DCIA:</b> {d['own_composite_dcia']:.2f}%;
        <b>Total area:</b> {d['own_total_area_ac']:.2f} ac
      </p>
      <div class='flow-summary'>
        <b>Output flow (own + upstream):</b><br>
        Volume: {fmt_volume_html(trace.outputs_summary['volume_acft'])};
        TN: {fmt_load_html(trace.outputs_summary['tn_lb'])};
        TP: {fmt_load_html(trace.outputs_summary['tp_lb'])}
      </div>
      {render_node_warnings(trace)}
    </div>
    """


def render_dry_retention_trace(trace):
    d = trace.calculation_details
    if d.get('no_upstream'):
        return f"""
        <div class='node-section'>
          <h3><span class='node-id'>{esc(trace.node_id)}</span>
              <span class='node-type'>dry retention</span>
              {esc(trace.node_name)}</h3>
          {render_notes_block(trace.node_notes, 'BMP notes')}
          <p>This BMP has no upstream contributing area; calculation skipped.</p>
          {render_node_warnings(trace)}
        </div>
        """
    return f"""
    <div class='node-section'>
      <h3><span class='node-id'>{esc(trace.node_id)}</span>
          <span class='node-type'>dry retention</span>
          {esc(trace.node_name)}</h3>
      {render_notes_block(trace.node_notes, 'BMP notes')}
      <p>
        <span class='citation'>Calculation per AH §9.5 / Appendix O —
        Dry Retention Systems (Mean Annual Mass Removal Efficiency tables).
        Composite CN/DCIA at junctions follow the area-weighting approach
        with DCIA → 0 for streams that previously passed through dry retention.</span>
      </p>
      <div class='flow-summary'>
        <b>Inflow:</b>
        Volume {fmt_volume_html(trace.inputs_summary['volume_acft'])};
        TN {fmt_load_html(trace.inputs_summary['tn_lb'])};
        TP {fmt_load_html(trace.inputs_summary['tp_lb'])}<br>
        From: {', '.join(esc(c) for c in trace.inputs_summary['components']) or '(none)'}
      </div>
      <table class='calc-table'>
        <tr><th>Parameter</th><th>Value</th><th>Source / Notes</th></tr>
        <tr><td>Storage volume</td><td class='num'>{d['storage_acft']:.3f} ac-ft</td>
            <td>From project file (treatment volume above pond bottom + unsaturated soil to seasonal high water table)</td></tr>
        <tr><td>Contributing area</td><td class='num'>{d['contributing_area_ac']:.2f} ac</td>
            <td>Sum of upstream catchment areas</td></tr>
        <tr><td>Retention depth</td><td class='num'>{d['retention_depth_in']:.3f} in</td>
            <td>= storage × 12 / area</td></tr>
        <tr><td>Composite CN (at this BMP)</td><td class='num'>{d['composite_cn']:.2f}</td>
            <td>Area-weighted across upstream subareas</td></tr>
        <tr><td>Composite DCIA (at this BMP)</td><td class='num'>{d['composite_dcia']:.2f}%</td>
            <td>Area-weighted; zeroed for streams already through dry retention</td></tr>
        <tr><td><b>Efficiency</b></td><td class='num'><b>{d['efficiency_pct']:.2f}%</b></td>
            <td>{esc(d['method'])}</td></tr>
        <tr><td>Volume captured</td><td class='num'>{d['volume_captured_acft']:.3f} ac-ft/yr</td>
            <td>= inflow × efficiency. This water infiltrates and does not discharge.</td></tr>
        <tr><td>TN captured</td><td class='num'>{d['tn_captured_lb']:.2f} lb/yr</td><td></td></tr>
        <tr><td>TP captured</td><td class='num'>{d['tp_captured_lb']:.2f} lb/yr</td><td></td></tr>
      </table>
      <div class='flow-summary'>
        <b>Discharge:</b>
        Volume {fmt_volume_html(trace.outputs_summary['volume_acft'])};
        TN {fmt_load_html(trace.outputs_summary['tn_lb'])};
        TP {fmt_load_html(trace.outputs_summary['tp_lb'])}
      </div>
      {render_node_warnings(trace)}
    </div>
    """


def render_wet_detention_trace(trace):
    d = trace.calculation_details
    if d.get('no_upstream'):
        return f"""
        <div class='node-section'>
          <h3><span class='node-id'>{esc(trace.node_id)}</span>
              <span class='node-type'>wet detention</span>
              {esc(trace.node_name)}</h3>
          {render_notes_block(trace.node_notes, 'BMP notes')}
          <p>This BMP has no upstream volume; calculation skipped.</p>
          {render_node_warnings(trace)}
        </div>
        """

    credit_notes = ""
    if d.get('littoral_notes'):
        credit_notes += f"<div class='notes'><b>Littoral zone notes:</b> {esc(d['littoral_notes'])}</div>"
    if d.get('floating_islands_notes'):
        credit_notes += f"<div class='notes'><b>Floating islands notes:</b> {esc(d['floating_islands_notes'])}</div>"
    if d.get('alum_notes'):
        credit_notes += f"<div class='notes'><b>Alum injection notes:</b> {esc(d['alum_notes'])}</div>"

    return f"""
    <div class='node-section'>
      <h3><span class='node-id'>{esc(trace.node_id)}</span>
          <span class='node-type'>wet detention</span>
          {esc(trace.node_name)}</h3>
      {render_notes_block(trace.node_notes, 'BMP notes')}
      <p>
        <span class='citation'>Calculation per AH §9.5 / FDEP detention methodology.
        Base efficiencies from Harper &amp; Baker (2007) curves;
        credits compounded via Eq. 9-5 (Treatment Train Efficiency).</span>
      </p>
      <div class='equation'>td (days) = Permanent Pool (ac-ft) / Annual Inflow (ac-ft/yr) × 365</div>
      <div class='equation'>Base TN % = 43.75 × td / (4.38 + td)</div>
      <div class='equation'>Base TP % = 40.13 + 6.372 × ln(td) + 0.213 × (ln td)²</div>
      <div class='equation'>Eff_total = 1 - (1 - base) × (1 - littoral) × (1 - floating) × (1 - alum)</div>
      <div class='flow-summary'>
        <b>Inflow:</b>
        Volume {fmt_volume_html(trace.inputs_summary['volume_acft'])};
        TN {fmt_load_html(trace.inputs_summary['tn_lb'])};
        TP {fmt_load_html(trace.inputs_summary['tp_lb'])}<br>
        From: {', '.join(esc(c) for c in trace.inputs_summary['components']) or '(none)'}
      </div>
      <table class='calc-table'>
        <tr><th>Parameter</th><th class='num'>Value</th><th>Source / Notes</th></tr>
        <tr><td>Permanent pool volume</td><td class='num'>{d['permanent_pool_acft']:.3f} ac-ft</td>
            <td>From project file</td></tr>
        <tr><td>Annual inflow volume</td><td class='num'>{d['inflow_volume_acft_yr']:.3f} ac-ft/yr</td>
            <td>Sum of upstream</td></tr>
        <tr><td>Residence time (td)</td><td class='num'>{d['residence_time_days']:.2f} days</td>
            <td>= pool / inflow × 365</td></tr>
        <tr><td>Base TN efficiency</td><td class='num'>{d['base_tn_pct']:.2f}%</td>
            <td>43.75·td/(4.38+td)</td></tr>
        <tr><td>Base TP efficiency</td><td class='num'>{d['base_tp_pct']:.2f}%</td>
            <td>40.13 + 6.372·ln(td) + 0.213·(ln td)²</td></tr>
        <tr><td>Littoral zone credit</td><td class='num'>{d['littoral_credit_pct']:.2f}%</td>
            <td>Per AH Vol II (max 10% suggested)</td></tr>
        <tr><td>Floating islands coverage</td><td class='num'>{d['floating_islands_pct_coverage']:.2f}%</td>
            <td>Per Wanielista &amp; Chang 2012</td></tr>
        <tr><td>Floating islands credit</td><td class='num'>{d['floating_islands_credit_pct']:.2f}%</td>
            <td>{esc(d['floating_islands_source'])}</td></tr>
        <tr><td>Alum injection credit (TN)</td><td class='num'>{d['alum_tn_pct']:.2f}%</td>
            <td>From jar testing (Harper &amp; Herr 1998)</td></tr>
        <tr><td>Alum injection credit (TP)</td><td class='num'>{d['alum_tp_pct']:.2f}%</td>
            <td>From jar testing (Harper &amp; Herr 1998)</td></tr>
        <tr><td><b>Combined TN efficiency</b></td><td class='num'><b>{d['eff_tn_pct']:.2f}%</b></td>
            <td>Eq. 9-5 compounding</td></tr>
        <tr><td><b>Combined TP efficiency</b></td><td class='num'><b>{d['eff_tp_pct']:.2f}%</b></td>
            <td>Eq. 9-5 compounding</td></tr>
        <tr><td>TN captured</td><td class='num'>{d['tn_captured_lb']:.2f} lb/yr</td><td></td></tr>
        <tr><td>TP captured</td><td class='num'>{d['tp_captured_lb']:.2f} lb/yr</td><td></td></tr>
      </table>
      {credit_notes}
      <div class='flow-summary'>
        <b>Discharge:</b>
        Volume {fmt_volume_html(trace.outputs_summary['volume_acft'])} (≈ inflow; wet detention does not reduce annual volume);
        TN {fmt_load_html(trace.outputs_summary['tn_lb'])};
        TP {fmt_load_html(trace.outputs_summary['tp_lb'])}
      </div>
      {render_node_warnings(trace)}
    </div>
    """


def render_other_bmp_trace(trace):
    d = trace.calculation_details
    return f"""
    <div class='node-section'>
      <h3><span class='node-id'>{esc(trace.node_id)}</span>
          <span class='node-type'>other BMP</span>
          {esc(trace.node_name)}</h3>
      {render_notes_block(trace.node_notes, 'BMP notes')}
      <p>
        <span class='citation'>Flat-percentage removal applied per AH Appendix O.
        Volume passes through unchanged.</span>
      </p>
      <table class='calc-table'>
        <tr><th>Parameter</th><th>Value</th><th>Source</th></tr>
        <tr><td>Preset</td><td>{esc(d['preset_name'])} ({esc(d['preset'])})</td>
            <td>{esc(d['source'])}</td></tr>
        <tr><td>Default TN % (preset)</td><td class='num'>{d['preset_default_tn_pct']:.2f}%</td><td></td></tr>
        <tr><td>Default TP % (preset)</td><td class='num'>{d['preset_default_tp_pct']:.2f}%</td><td></td></tr>
        <tr><td><b>TN removal applied</b></td><td class='num'><b>{d['tn_removal_pct']:.2f}%</b></td>
            <td>From project file</td></tr>
        <tr><td><b>TP removal applied</b></td><td class='num'><b>{d['tp_removal_pct']:.2f}%</b></td>
            <td>From project file</td></tr>
        <tr><td>TN captured</td><td class='num'>{d['tn_captured_lb']:.2f} lb/yr</td><td></td></tr>
        <tr><td>TP captured</td><td class='num'>{d['tp_captured_lb']:.2f} lb/yr</td><td></td></tr>
      </table>
      <div class='flow-summary'>
        <b>Inflow:</b> Volume {fmt_volume_html(trace.inputs_summary['volume_acft'])};
        TN {fmt_load_html(trace.inputs_summary['tn_lb'])};
        TP {fmt_load_html(trace.inputs_summary['tp_lb'])}<br>
        <b>Discharge:</b> Volume {fmt_volume_html(trace.outputs_summary['volume_acft'])};
        TN {fmt_load_html(trace.outputs_summary['tn_lb'])};
        TP {fmt_load_html(trace.outputs_summary['tp_lb'])}
      </div>
      {render_node_warnings(trace)}
    </div>
    """


def render_outfall_trace(trace):
    return f"""
    <div class='node-section'>
      <h3><span class='node-id'>{esc(trace.node_id)}</span>
          <span class='node-type'>outfall</span>
          {esc(trace.node_name)}</h3>
      {render_notes_block(trace.node_notes, 'Outfall notes')}
      <div class='flow-summary'>
        <b>Discharge to receiving water:</b><br>
        Volume: {fmt_volume_html(trace.inputs_summary['volume_acft'])}<br>
        TN: {fmt_load_html(trace.inputs_summary['tn_lb'])}<br>
        TP: {fmt_load_html(trace.inputs_summary['tp_lb'])}<br>
        From upstream: {', '.join(esc(c) for c in trace.inputs_summary['components'])}
      </div>
    </div>
    """


def render_node_warnings(trace):
    if not trace.warnings:
        return ""
    items = "".join(f"<div class='warning'>⚠ {esc(w)}</div>" for w in trace.warnings)
    return items


def render_summary(result):
    base_tn = result['baseline_tn_lb']
    base_tp = result['baseline_tp_lb']
    base_v = result['baseline_volume_acft']

    rows = []
    total_v = total_tn = total_tp = 0.0
    for oid, r in result['outfall_results'].items():
        rows.append(f"<tr>"
                    f"<td>{esc(oid)} — {esc(r['name'])}</td>"
                    f"<td class='num'>{r['volume_acft']:.3f}</td>"
                    f"<td class='num'>{r['tn_lb']:.2f} ({lb_to_kg(r['tn_lb']):.2f})</td>"
                    f"<td class='num'>{r['tp_lb']:.2f} ({lb_to_kg(r['tp_lb']):.2f})</td>"
                    f"</tr>")
        total_v += r['volume_acft']
        total_tn += r['tn_lb']
        total_tp += r['tp_lb']
    rows.append(f"<tr style='font-weight:bold;background:var(--druid-paper-2);'>"
                f"<td>All outfalls (project total)</td>"
                f"<td class='num'>{total_v:.3f}</td>"
                f"<td class='num'>{total_tn:.2f} ({lb_to_kg(total_tn):.2f})</td>"
                f"<td class='num'>{total_tp:.2f} ({lb_to_kg(total_tp):.2f})</td>"
                f"</tr>")

    tn_red = (1 - total_tn / base_tn) * 100 if base_tn > 0 else 0
    tp_red = (1 - total_tp / base_tp) * 100 if base_tp > 0 else 0

    return f"""
    <h2>Summary</h2>

    <h3>Baseline (sum of catchment generation, before any treatment)</h3>
    <table class='summary-table'>
      <tr><th>Volume</th><th class='num'>{base_v:.3f} ac-ft/yr</th></tr>
      <tr><th>TN load</th><th class='num'>{base_tn:.2f} lb/yr ({lb_to_kg(base_tn):.2f} kg/yr)</th></tr>
      <tr><th>TP load</th><th class='num'>{base_tp:.2f} lb/yr ({lb_to_kg(base_tp):.2f} kg/yr)</th></tr>
    </table>

    <h3>Discharge at outfalls</h3>
    <table class='summary-table'>
      <tr><th>Outfall</th><th class='num'>Volume (ac-ft/yr)</th>
          <th class='num'>TN (lb/yr / kg/yr)</th>
          <th class='num'>TP (lb/yr / kg/yr)</th></tr>
      {''.join(rows)}
    </table>

    <h3>Reductions (baseline → outfalls)</h3>
    <table class='summary-table'>
      <tr><th>TN reduction</th><th class='num'>{tn_red:.2f}%</th></tr>
      <tr><th>TP reduction</th><th class='num'>{tp_red:.2f}%</th></tr>
    </table>
    <p style='color: #555; font-size: .92em;'>
      The engineer should compare these reductions to the applicable
      §8.3 performance standard for their HUC 12 watershed (general,
      OFW, impaired, redevelopment, etc.). The post-development load
      should also be compared to predevelopment load via a separately
      run pre-development project file, if applicable.
    </p>
    """


# ---------------------------------------------------------------------
# Connectivity diagram (auto-laid-out SVG)
# ---------------------------------------------------------------------

# Color palette for node types in the diagram. Harmonizes with the
# Druid brand palette: forest, sage, gold, charcoal, plus a muted
# blue-gray for wet detention to reflect the "water" character.
NODE_COLORS = {
    'catchment':     {'fill': '#7FB9A3', 'stroke': '#0F3F37'},  # sage / forest
    'dry_retention': {'fill': '#B8956A', 'stroke': '#6B4F2A'},  # warm sand / earth
    'wet_detention': {'fill': '#6B92A8', 'stroke': '#2F4858'},  # muted blue / steel
    'other_bmp':     {'fill': '#D9B85F', 'stroke': '#8A6F2A'},  # gold
    'outfall':       {'fill': '#101416', 'stroke': '#000000'},  # charcoal
}

NODE_TYPE_LABELS = {
    'catchment':     'Catchment',
    'dry_retention': 'Dry Retention',
    'wet_detention': 'Wet Detention',
    'other_bmp':     'Other BMP',
    'outfall':       'Outfall',
}


def render_connectivity_svg(project, result):
    """Render an auto-laid-out flow diagram as inline SVG.

    Layout algorithm:
      1. Rank each node by its longest path-distance to a sink (an outfall
         or other terminal node). Sinks sit at the rightmost column;
         nodes upstream are placed further left, ONE column away from
         their target whenever possible.

         This is a backward-distance ranking. The earlier forward-longest-
         path ranking placed all sources in column 0, which forced multi-
         column-skip arrows when a source's target wasn't itself a
         long-path node — producing ugly S-curves around intermediate nodes.
         Backward-distance ranking puts each source one column upstream of
         its target, so most arrows are short and nearly horizontal.

      2. Within each column, order nodes by the average vertical position
         of their successors (a barycenter heuristic). This pulls nodes
         vertically close to what they drain into and reduces edge crossings.

      3. Draw rounded rectangles for nodes and bezier arrows for drains_to
         edges. Adjacent-column edges become near-horizontal; multi-column-
         skip edges (still possible if intentionally drawn that way in the
         project) gracefully curve around.

    The diagram is purely topological — actual `position` fields in
    the JSON are ignored here.
    """
    nodes = project['nodes']
    nodes_by_id = {n['id']: n for n in nodes}

    # Build forward adjacency
    successors = {n['id']: [] for n in nodes}
    for n in nodes:
        dt = n.get('drains_to')
        if dt and dt in nodes_by_id:
            successors[n['id']].append(dt)

    # ---- Rank: longest forward path from each node to a sink ----
    # Sinks (no successors) get rank 0; everyone else gets 1 + max(rank
    # of successors). Memoized recursion. The visiting set is a defensive
    # cycle guard; valid projects shouldn't hit it because the engine
    # rejects cycles before we render.
    rank = {}

    def compute_rank(nid, visiting):
        if nid in rank:
            return rank[nid]
        if nid in visiting:
            return 0  # defensive: cycle, return 0 and break recursion
        visiting.add(nid)
        succs = successors[nid]
        if not succs:
            r = 0
        else:
            r = 1 + max(compute_rank(s, visiting) for s in succs)
        visiting.remove(nid)
        rank[nid] = r
        return r

    for nid in nodes_by_id:
        compute_rank(nid, set())

    # Column index: max_rank - rank, so sinks are at the rightmost column.
    max_rank = max(rank.values()) if rank else 0
    column = {nid: max_rank - rank[nid] for nid in nodes_by_id}

    # Group nodes by column
    by_col = {}
    for nid, c in column.items():
        by_col.setdefault(c, []).append(nid)

    # ---- Layout dimensions ----
    NODE_W = 130
    NODE_H = 50
    COL_GAP = 60
    ROW_GAP = 18
    PAD = 20

    max_col = max(by_col.keys()) if by_col else 0
    max_per_col = max(len(by_col[c]) for c in by_col) if by_col else 1

    width  = PAD * 2 + (max_col + 1) * NODE_W + max_col * COL_GAP
    height = PAD * 2 + max_per_col * NODE_H + (max_per_col - 1) * ROW_GAP

    # ---- Vertical ordering within columns ----
    # We process columns RIGHT to LEFT. The rightmost column (sinks) is
    # sorted alphabetically. Each column to the left is then sorted by the
    # average y-position of its successors in already-placed columns
    # (barycenter heuristic). Ties broken by id for determinism.

    coords = {}
    # Place rightmost column first (alphabetical)
    sorted_cols = sorted(by_col.keys(), reverse=True)  # right → left

    for c in sorted_cols:
        nids = by_col[c]
        if c == max_col:
            # Rightmost column: just sort by id
            nids.sort()
        else:
            # Sort by average y of successors. If a node has no successor
            # in the next column (already-placed), fall back to a high
            # value to push it down (rare; mostly happens for orphans).
            def barycenter(nid):
                succ_ys = []
                for s in successors[nid]:
                    if s in coords:
                        # use the y-center of the successor
                        succ_ys.append(coords[s][1] + NODE_H / 2)
                if not succ_ys:
                    return float('inf')
                return sum(succ_ys) / len(succ_ys)
            nids.sort(key=lambda n: (barycenter(n), n))

        # Now compute y-coordinates for this column (vertically centered)
        col_h = len(nids) * NODE_H + (len(nids) - 1) * ROW_GAP
        y0 = PAD + (height - 2 * PAD - col_h) / 2
        x = PAD + c * (NODE_W + COL_GAP)
        for i, nid in enumerate(nids):
            y = y0 + i * (NODE_H + ROW_GAP)
            coords[nid] = (x, y)

    # Build outfall result lookup (so we can show numbers on outfalls)
    outfall_results = result.get('outfall_results', {}) if result else {}

    # Build SVG
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" style="max-width:100%;">'
    ]
    # Arrow marker
    svg_parts.append(
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#444"/>'
        '</marker>'
        '</defs>'
    )

    # Draw edges first (so they sit behind nodes)
    for n in nodes:
        dt = n.get('drains_to')
        if not dt or dt not in coords:
            continue
        x1, y1 = coords[n['id']]
        x2, y2 = coords[dt]
        # Edge from right side of source to left side of target
        sx = x1 + NODE_W
        sy = y1 + NODE_H / 2
        tx = x2
        ty = y2 + NODE_H / 2
        # Bezier curve for nicer look
        mid_x = (sx + tx) / 2
        path_d = f"M {sx} {sy} C {mid_x} {sy}, {mid_x} {ty}, {tx} {ty}"
        svg_parts.append(
            f'<path d="{path_d}" stroke="#444" stroke-width="1.6" '
            f'fill="none" marker-end="url(#arrow)"/>'
        )

    # Draw nodes
    for n in nodes:
        nid = n['id']
        if nid not in coords:
            continue
        x, y = coords[nid]
        c = NODE_COLORS.get(n['type'], NODE_COLORS['outfall'])
        type_label = NODE_TYPE_LABELS.get(n['type'], n['type'])

        # Rounded rect
        svg_parts.append(
            f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" '
            f'rx="6" ry="6" fill="{c["fill"]}" stroke="{c["stroke"]}" '
            f'stroke-width="1.5"/>'
        )
        # Choose text color based on background lightness (charcoal & forest get paper text)
        text_color = "#F4F1E8" if n['type'] in ('outfall',) else "#101416"
        # Node id (top line, monospace, smaller)
        svg_parts.append(
            f'<text x="{x + NODE_W/2}" y="{y + 16}" text-anchor="middle" '
            f'font-family="JetBrains Mono, Consolas, monospace" '
            f'font-size="11" font-weight="700" fill="{text_color}">'
            f'{esc(nid)}</text>'
        )
        # Node name (middle line)
        name = n.get('name') or ''
        # Truncate long names
        if len(name) > 22:
            name = name[:20] + '…'
        svg_parts.append(
            f'<text x="{x + NODE_W/2}" y="{y + 31}" text-anchor="middle" '
            f'font-family="Source Sans 3, Arial, sans-serif" '
            f'font-size="10.5" fill="{text_color}">'
            f'{esc(name)}</text>'
        )
        # Type label (bottom line, italic, smaller)
        svg_parts.append(
            f'<text x="{x + NODE_W/2}" y="{y + 44}" text-anchor="middle" '
            f'font-family="Source Sans 3, Arial, sans-serif" '
            f'font-size="9" font-style="italic" fill="{text_color}" opacity="0.85">'
            f'{esc(type_label)}</text>'
        )

    svg_parts.append('</svg>')

    return "<div class='connectivity-diagram'>" + "".join(svg_parts) + "</div>"


# ---------------------------------------------------------------------
# Branded footer
# ---------------------------------------------------------------------

def render_brand_footer():
    """The footer brand mark with trademark notice, license, version, and
    engineering disclaimer.

    This footer appears on every generated report (full and summary). It
    serves as the canonical attribution + disclaimer block — the kind of
    boilerplate a permit reviewer would expect to see on engineering
    output produced by a tool. Components:

      - Brand: SAGE (with TM superscript on first use), tagline, version
      - Attribution: by DRUID (TM), link to source
      - License: Apache-2.0 with link to LICENSE
      - Methodology citation: FDEP AH Vol I
      - Engineering disclaimer: independent verification, not agency approval
    """
    return f"""
    <div class='brand-footer'>
      <div class='brand-name'>SAGE<sup style='font-size:.55em; vertical-align: super; margin-left:1px;'>TM</sup></div>
      <div class='tagline'>Stormwater Analysis &amp; GSI Evaluator — version {esc(__version__)}</div>
      <div style='margin-top: .5em;'>
        by <b>DRUID<sup style='font-size:.7em; vertical-align: super; margin-left:1px;'>TM</sup></b>
        — <a href='https://druid.solutions' style='color: var(--druid-forest);'>druid.solutions</a>
      </div>
      <div style='margin-top: .4em; color:#666; font-size:.85em;'>
        Licensed under
        <a href='https://www.apache.org/licenses/LICENSE-2.0' style='color: var(--druid-forest);'>Apache License 2.0</a>.
        SAGE and DRUID are trademarks of Druid.
      </div>
      <div style='margin-top: .8em; color:#666; font-size:.85em;'>
        Calculations follow the Florida DEP Applicant's Handbook Vol I (June 28, 2024).
        This software is not produced by, endorsed by, or affiliated with FDEP.
      </div>
      <div style='margin-top: .8em; padding: .6em .8em; background: var(--druid-paper-2);
                  border-left: 3px solid var(--druid-gold); font-size: .85em;
                  color: var(--druid-charcoal); text-align: left;'>
        <b>Engineering disclaimer.</b> Generated results are provided for
        engineering use and must be independently verified by the user or
        responsible professional. Users are responsible for confirming
        inputs, assumptions, calculations, site applicability, and regulatory
        acceptance. Use of this software does not constitute agency approval
        or a guarantee of compliance.
      </div>
    </div>
    """


# ---------------------------------------------------------------------
# Summary (one-page) report
# ---------------------------------------------------------------------

def render_summary_report_body(project, result):
    """Render the body of a one-page summary report (cover sheet style).

    Includes: project header, total catchment area, baseline vs. outfall
    totals, percent reductions, connectivity diagram, and warning count.
    Designed to fit on a single 8.5x11 sheet.
    """
    meta = project['project']

    # Compute total area across all catchments (sum of subareas)
    total_area = 0.0
    catchment_count = 0
    bmp_count = 0
    for n in project['nodes']:
        if n['type'] == 'catchment':
            catchment_count += 1
            for sub in n.get('subareas', []):
                total_area += sub.get('area_ac', 0)
        elif n['type'] in ('dry_retention', 'wet_detention', 'other_bmp'):
            bmp_count += 1
    outfall_count = sum(1 for n in project['nodes'] if n['type'] == 'outfall')

    # Outfall totals
    total_v = total_tn = total_tp = 0.0
    outfall_rows = []
    for oid, r in result['outfall_results'].items():
        outfall_rows.append(
            f"<tr><td>{esc(oid)} — {esc(r['name'])}</td>"
            f"<td class='num'>{r['volume_acft']:.2f}</td>"
            f"<td class='num'>{r['tn_lb']:.2f} ({lb_to_kg(r['tn_lb']):.2f})</td>"
            f"<td class='num'>{r['tp_lb']:.2f} ({lb_to_kg(r['tp_lb']):.2f})</td></tr>"
        )
        total_v += r['volume_acft']
        total_tn += r['tn_lb']
        total_tp += r['tp_lb']
    if len(result['outfall_results']) > 1:
        outfall_rows.append(
            f"<tr style='font-weight:bold;background:var(--druid-paper-2);'>"
            f"<td>All outfalls</td>"
            f"<td class='num'>{total_v:.2f}</td>"
            f"<td class='num'>{total_tn:.2f} ({lb_to_kg(total_tn):.2f})</td>"
            f"<td class='num'>{total_tp:.2f} ({lb_to_kg(total_tp):.2f})</td></tr>"
        )

    base_tn = result['baseline_tn_lb']
    base_tp = result['baseline_tp_lb']
    base_v  = result['baseline_volume_acft']
    tn_red = (1 - total_tn / base_tn) * 100 if base_tn > 0 else 0
    tp_red = (1 - total_tp / base_tp) * 100 if base_tp > 0 else 0

    # Warnings count
    n_warnings = len(result.get('warnings', []))
    for t in result['traces']:
        n_warnings += len(t.warnings)
    warnings_note = (
        f"<span style='color:#a60;'><b>{n_warnings}</b> warning(s)"
        f" — see full report for details.</span>"
        if n_warnings > 0 else
        "<span style='color:var(--druid-forest);'>No warnings.</span>"
    )

    return f"""
    <h1>SAGE Project Summary</h1>
    <table class='meta-table'>
      <tr>
        <th style='width:18%;'>Project</th><td><b>{esc(meta.get('name'))}</b></td>
        <th style='width:14%;'>Zone</th><td style='width:8%;'>{esc(meta.get('zone'))}</td>
        <th style='width:14%;'>Rainfall</th><td style='width:14%;'>{esc(meta.get('annual_rainfall_in'))} in/yr</td>
      </tr>
      <tr>
        <th>Applicant</th><td>{esc(meta.get('applicant'))}</td>
        <th>Date</th><td colspan='3'>{esc(meta.get('date_modified') or meta.get('date_created') or '')}</td>
      </tr>
    </table>

    <h2>Site overview</h2>
    <table class='summary-table'>
      <tr>
        <th class='num'>Catchments</th><td class='num'>{catchment_count}</td>
        <th class='num'>Total area (ac)</th><td class='num'>{total_area:.2f}</td>
        <th class='num'>BMPs</th><td class='num'>{bmp_count}</td>
        <th class='num'>Outfalls</th><td class='num'>{outfall_count}</td>
      </tr>
    </table>

    <h2>Connectivity</h2>
    {render_connectivity_svg(project, result)}

    <h2>Loadings</h2>
    <table class='summary-table'>
      <tr>
        <th></th>
        <th class='num'>Volume (ac-ft/yr)</th>
        <th class='num'>TN (lb/yr / kg/yr)</th>
        <th class='num'>TP (lb/yr / kg/yr)</th>
      </tr>
      <tr>
        <td><b>Untreated baseline</b></td>
        <td class='num'>{base_v:.2f}</td>
        <td class='num'>{base_tn:.2f} ({lb_to_kg(base_tn):.2f})</td>
        <td class='num'>{base_tp:.2f} ({lb_to_kg(base_tp):.2f})</td>
      </tr>
      {''.join(outfall_rows)}
    </table>

    <h2>Total reductions (baseline → outfalls)</h2>
    <table class='summary-table'>
      <tr>
        <th class='num' style='width:50%;'>TN reduction</th>
        <th class='num' style='width:50%;'>TP reduction</th>
      </tr>
      <tr>
        <td class='num' style='font-size:1.4em; color:var(--druid-forest); font-weight:700;'>{tn_red:.2f}%</td>
        <td class='num' style='font-size:1.4em; color:var(--druid-forest); font-weight:700;'>{tp_red:.2f}%</td>
      </tr>
    </table>

    <p style='font-size: .92em; margin-top: 1em;'>
      {warnings_note}
      Compare these reductions to the applicable §8.3 performance standard
      for the project's HUC 12 watershed. Post-development load should also
      be compared to pre-development load if applicable. See the full audit
      report for per-node calculation details and citations.
    </p>
    """


# ---------------------------------------------------------------------
# Main report assembly
# ---------------------------------------------------------------------

def write_html_report(out_path, project, result, style='full'):
    """Generate the HTML report and write it to disk.

    Args:
      out_path: filesystem path to write to
      project: the project dict
      result: the calculated result dict (from engine.run_calculations)
      style: 'full' (default, complete walkthrough) or 'summary' (one-page cover)
    """
    html_doc = generate_html_report(project, result, style=style)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_doc)


def generate_html_report(project, result, style='full'):
    """Build the HTML report as a string. Returns the document.

    Used by both write_html_report() and the HTTP server (which sends
    the bytes directly to the browser without writing to disk).
    """
    if style == 'summary':
        return _build_summary_html(project, result)
    return _build_full_html(project, result)


def _build_summary_html(project, result):
    """Build the one-page summary report HTML."""
    body_html = render_summary_report_body(project, result)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SAGE Summary — {esc(project['project'].get('name', ''))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Source+Serif+4:ital@0;1&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body class="summary">
{body_html}
{render_brand_footer()}
</body>
</html>
"""


def _build_full_html(project, result):
    """Build the complete walkthrough report HTML."""
    parts = [render_header(project)]

    # Collect all warnings for the top-level warnings section
    all_warnings = list(result['warnings'])
    for t in result['traces']:
        for w in t.warnings:
            all_warnings.append(f"[{t.node_id} — {t.node_name}] {w}")
    parts.append(render_warnings_block(all_warnings))

    # Connectivity diagram for visual reference
    parts.append("<h2>Connectivity</h2>")
    parts.append(render_connectivity_svg(project, result))

    # Per-node trace
    parts.append("<h2>Calculation walkthrough</h2>")
    parts.append("<p>Nodes are shown in evaluation order (upstream first). "
                 "Each section cites the AH equation/table used.</p>")

    for t in result['traces']:
        if t.node_type == 'catchment':
            parts.append(render_catchment_trace(t))
        elif t.node_type == 'dry_retention':
            parts.append(render_dry_retention_trace(t))
        elif t.node_type == 'wet_detention':
            parts.append(render_wet_detention_trace(t))
        elif t.node_type == 'other_bmp':
            parts.append(render_other_bmp_trace(t))
        elif t.node_type == 'outfall':
            parts.append(render_outfall_trace(t))

    # Final summary
    parts.append(render_summary(result))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SAGE Audit Report — {esc(project['project'].get('name', ''))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Source+Serif+4:ital@0;1&family=JetBrains+Mono&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
{''.join(parts)}
{render_brand_footer()}
</body>
</html>
"""
