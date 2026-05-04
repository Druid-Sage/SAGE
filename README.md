# SAGE™ — Stormwater Analysis & GSI Evaluator

**Version 1.0.0** · Apache License 2.0 · A project of [DRUID™](https://druid.solutions)

A Python tool for FDEP stormwater nutrient loading and BMP treatment
train calculations, following the Florida DEP Applicant's Handbook
Volume I (June 28, 2024). Designed as an open replacement for BMPFAST.

---

## ⚠️ Engineering disclaimer

> **Generated results are provided for engineering use and must be
> independently verified by the user or responsible professional.
> Users are responsible for confirming inputs, assumptions, calculations,
> site applicability, and regulatory acceptance. Use of this software does
> not constitute agency approval or a guarantee of compliance.**

This is open-source engineering software, distributed without warranty.
Every report SAGE generates carries this disclaimer in the footer.

---

## What SAGE does

Takes a project description (catchments, BMPs, connectivity) — either
as a JSON file or via a browser-based UI — walks the routing graph,
and computes:

- Annual runoff volume per catchment (Appendix N ROC tables)
- Annual TN and TP loading per catchment (Table 9.2 EMCs × Eq. 9-2)
- BMP efficiencies for dry retention (Appendix O), wet detention
  (residence-time formulas), and "other" BMPs (flat percentages
  with presets and custom override)
- Combined treatment train efficiency via Eq. 9-5 compounding
- Per-outfall and project-total discharge

Produces an HTML audit report citing every AH equation, table, and
section used. Also produces a one-page summary report suitable as a
cover sheet for an appendix.

## Requirements

- Python 3.8 or newer (no external libraries — uses only the stdlib)
- A modern web browser (Chrome, Edge, Firefox, Safari) for the web UI
- That's it — no `pip install` required.

## Two ways to use SAGE

### Option A: Web UI (recommended)

1. Open a terminal and `cd` to this folder.
2. Start the server:
   ```
   python server.py
   ```
3. Open `http://localhost:8765/` in your browser.
4. Build your project visually:
   - Edit project metadata in the left column
   - Add catchments, BMPs, and outfalls with the +Add buttons
   - Select a node to edit its parameters in the middle column
   - Connect nodes via the "Drains to" dropdown on each node
   - Watch results update live in the right column as you type
5. Click **Save** to download the project as JSON.
6. Click **Summary** for a one-page report or **Full Report** for the
   complete audit walkthrough.

The browser auto-saves your work to localStorage, so closing the tab
won't lose anything. Press Ctrl+C in the terminal when you're done to
stop the server.

The server binds to `127.0.0.1` (localhost only) — it is not
accessible from other machines.

### Option B: Command line

If you prefer text/JSON workflows, or are scripting batch runs:

1. Validate the data tables (optional, one-time check):
   ```
   python validate_data.py
   ```
2. Run the test suite (optional, after any code changes):
   ```
   python tests.py
   ```
3. Run the engine on a project file:
   ```
   python engine.py example_project.json
   ```
   You'll see the console summary, and `example_project_report.html`
   will be created next to the JSON.

CLI flags:
- `--summary` — generate a one-page summary report instead of the
  full report
- `--both` — generate both the summary and full reports
- `--no-report` — console output only

## Making your own project

Either build it in the web UI (recommended for new projects) or copy
`example_project.json` to a new filename and edit by hand. The schema
is documented in `SCHEMA_v1.md`.

The minimum project needs:
- Project metadata (name, zone, annual rainfall)
- At least one catchment with one or more subareas
- At least one outfall
- A `drains_to` chain connecting them

## Files in this distribution

| File | Purpose |
|---|---|
| `server.py` | HTTP server for the web UI (run this to use the browser). |
| `engine.py` | Calculation engine; also the CLI entry point. |
| `tables.py` | Loads lookup CSVs, does interpolation. |
| `bmps.py` | Per-BMP-type calculation logic. |
| `report.py` | HTML report generator (full + summary modes). |
| `_version.py` | Single source of truth for the SAGE version string. |
| `tests.py` | Regression tests (run periodically to verify). |
| `validate_data.py` | Verifies data CSVs are correctly formatted. |
| `SCHEMA_v1.md` | Full project file schema specification. |
| `README.md` | This file. |
| `LICENSE` | Full Apache 2.0 license text. |
| `NOTICE` | Apache 2.0 NOTICE: copyright, trademarks, FDEP attribution. |
| `example_project.json` | Worked example matching our walkthrough. |
| `data/` | The 86 lookup table CSVs from FDEP AH Vol I. |
| `web/` | Static frontend (served by `server.py`). |
| `web/index.html` | The single-file SPA UI. |
| `web/assets/` | DRUID logo files. |

## Verifying the math

If you're skeptical of any number the tool produces, the full audit
report shows every step with citations. To double-check:

1. Generate the full HTML report (web UI: **Full Report** button;
   CLI: `python engine.py myproject.json`).
2. Open the HTML report in a browser.
3. Find the catchment or BMP in question.
4. Each section shows the input flow, the calculation parameters
   (with units), and the output flow.
5. The cited AH section/equation/table is shown next to each
   calculation.
6. You can manually look up the AH PDF or the relevant CSV file
   under `data/` to confirm the lookup value.

## Known limitations (V1.0)

- One project per file. Pre/post comparison is done by running two
  files separately and comparing.
- One downstream connection per node (`drains_to` is a single id).
  Splits / bypass weirs are not yet supported.
- No visual node-graph editor (drag-arrows-between-boxes UI). The
  current UI uses "drains to" dropdowns. A visual editor is on the
  roadmap for V2.0.
- No automatic performance-standard checking. The tool reports
  % removal; the engineer compares against §8.3 requirements
  (general 80/55, OFW 90/80, impaired 80/80, etc.) themselves.

## Reporting bugs

Open an issue on the project's GitHub repository. If a calculation
looks wrong, generate the full audit report and include the per-node
breakdown that explains the suspect number — that way the maintainer
can identify which step is off and check the corresponding lookup
table CSV against the AH PDF.

If you're reporting a likely engine bug, add a regression test to
`tests.py` covering the case before submitting a PR. The test suite
should grow as new edge cases are discovered.

## License

SAGE is released under the **Apache License, Version 2.0**. See the
[LICENSE](LICENSE) file for the full text. In summary:

- ✅ Free to use for any purpose, including commercial use
- ✅ Free to modify and redistribute
- ✅ Free to incorporate into proprietary projects
- ⚠️ Must preserve copyright notices and the `NOTICE` file in
  redistributions
- ⚠️ Modified files must carry a notice that you changed them
- ❌ Provided with NO WARRANTY (see disclaimer above)

The data tables under `data/` are transcribed from the Florida DEP
Applicant's Handbook Vol I, a public Florida government document.
See [`NOTICE`](NOTICE) for full attribution.

## Trademarks

**SAGE™** and **DRUID™** are unregistered trademarks of Druid.

The Apache 2.0 License (Section 6) does not grant permission to use
the SAGE or DRUID names except as required for describing the origin
of the Work and reproducing the content of the [`NOTICE`](NOTICE) file.
If you fork SAGE and distribute a modified version, please choose a
distinct name for your fork to avoid user confusion.

---

> Generated by **SAGE™** by **DRUID™**
> Official source: https://druid.solutions
> Version: 1.0.0
> Licensed under Apache-2.0
> Results must be independently verified by the user or responsible professional.
