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
server.py — Tiny HTTP server that fronts the SAGE calculation engine.

Architecture: the Python engine (engine.py + bmps.py + tables.py +
report.py) runs unchanged, and this server adds an HTTP layer on top.
The frontend (web/index.html) is served as a static file and talks to
the engine over a simple JSON API.

No external dependencies: uses only the standard library's http.server
module. This is deliberate so that the tool runs on locked-down work
laptops where pip-install is not allowed.

Usage:
    python server.py               # default port 8765
    python server.py --port 8000   # explicit port

After starting, open http://localhost:<port>/ in any modern browser.

API endpoints:
  GET  /                  → web/index.html
  GET  /assets/<file>     → web/assets/<file>  (logos)
  GET  /api/land_covers   → JSON list of EMC table land cover names
  GET  /api/presets       → JSON dict of other_bmp preset defaults
  POST /api/calculate     → body: project JSON; returns: result JSON
  POST /api/report        → body: {project, style}; returns: HTML

The server logs each request to stdout and handles errors by returning
JSON with an `error` key. It does not write any files; the frontend
handles save/load via browser file APIs.

Security note: this is intended for localhost use only. It binds to
127.0.0.1 by default. Do NOT expose to the internet.
"""

import argparse
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Engine modules (unchanged from V0.1)
import engine
import tables
import bmps
import report
from _version import __version__


# Paths
HERE = Path(__file__).parent
WEB_DIR = HERE / "web"
ASSETS_DIR = WEB_DIR / "assets"

# MIME types we serve as static files
STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class SageHandler(BaseHTTPRequestHandler):
    """One handler per request. Routes by method + path."""

    # ---- response helpers ----

    def _send(self, status, body, content_type="text/plain; charset=utf-8",
              extra_headers=None):
        """Send a complete response with body."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Tight CSP and no-cache for the local-tool use case
        self.send_header("Cache-Control", "no-cache")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status, message, detail=None):
        payload = {"error": message}
        if detail:
            payload["detail"] = detail
        self._send_json(status, payload)

    def _read_json_body(self):
        """Parse the request body as JSON. Returns dict or None."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Request body is not valid JSON: {e}")

    # ---- static file serving ----

    def _serve_static(self, file_path: Path):
        """Serve a file from disk. Path must be inside WEB_DIR."""
        # Resolve and check it's actually inside WEB_DIR (defense-in-depth
        # against path traversal, though there's no user input here)
        try:
            resolved = file_path.resolve()
            resolved.relative_to(WEB_DIR.resolve())
        except (ValueError, OSError):
            self._send_error_json(403, "Forbidden")
            return
        if not resolved.exists() or not resolved.is_file():
            self._send_error_json(404, f"Not found: {file_path.name}")
            return
        mime = STATIC_MIME.get(resolved.suffix.lower(), "application/octet-stream")
        with open(resolved, "rb") as f:
            data = f.read()
        self._send(200, data, mime)

    # ---- routing ----

    def do_GET(self):
        path = self.path.split("?", 1)[0]  # strip query string

        if path == "/" or path == "/index.html":
            self._serve_static(WEB_DIR / "index.html")
            return

        if path == "/favicon.ico":
            # Serve the Druid creature as the favicon
            self._serve_static(ASSETS_DIR / "druid_creature.png")
            return

        if path.startswith("/assets/"):
            fname = path[len("/assets/"):]
            self._serve_static(ASSETS_DIR / fname)
            return

        if path == "/api/land_covers":
            try:
                covers = tables.list_land_covers()
                # Also send EMC values so the UI can display defaults
                # without a second round-trip.
                emc_data = {c: tables.lookup_emc(c) for c in covers}
                self._send_json(200, {
                    "land_covers": covers,
                    "emc": {c: {"tn_mgL": tn, "tp_mgL": tp}
                            for c, (tn, tp) in emc_data.items()},
                })
            except Exception as e:
                self._send_error_json(500, str(e))
            return

        if path == "/api/presets":
            self._send_json(200, bmps.OTHER_BMP_PRESETS)
            return

        if path == "/api/health":
            self._send_json(200, {
                "status": "ok",
                "engine": "SAGE",
                "version": __version__,
                "license": "Apache-2.0",
            })
            return

        self._send_error_json(404, f"Not found: {path}")

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        try:
            body = self._read_json_body()
        except ValueError as e:
            self._send_error_json(400, str(e))
            return

        if path == "/api/calculate":
            self._handle_calculate(body)
            return

        if path == "/api/report":
            self._handle_report(body)
            return

        self._send_error_json(404, f"Not found: {path}")

    # ---- API handlers ----

    def _handle_calculate(self, project):
        """Run engine on the supplied project. Return result as JSON."""
        if not isinstance(project, dict):
            self._send_error_json(400, "Request body must be a project object.")
            return
        try:
            result = engine.calculate(project)
            serial = engine.result_to_serializable(result)
            self._send_json(200, serial)
        except engine.ValidationError as e:
            # Validation errors are user-fixable: return 400 with the message
            self._send_json(400, {"error": "Validation error", "detail": str(e)})
        except Exception as e:
            # Unexpected: return 500 with traceback for debugging
            tb = traceback.format_exc()
            print(f"Engine error: {tb}", file=sys.stderr)
            self._send_json(500, {
                "error": "Engine error",
                "detail": str(e),
                "traceback": tb,
            })

    def _handle_report(self, body):
        """Generate the HTML report (full or summary). Return as text/html."""
        if not isinstance(body, dict) or "project" not in body:
            self._send_error_json(400, "Request body must include 'project'.")
            return
        project = body["project"]
        style = body.get("style", "full")
        if style not in ("full", "summary"):
            self._send_error_json(400, "style must be 'full' or 'summary'.")
            return
        try:
            result = engine.calculate(project)
            html = report.generate_html_report(project, result, style=style)
            self._send(200, html, "text/html; charset=utf-8")
        except engine.ValidationError as e:
            self._send_json(400, {"error": "Validation error", "detail": str(e)})
        except Exception as e:
            tb = traceback.format_exc()
            print(f"Report error: {tb}", file=sys.stderr)
            self._send_json(500, {
                "error": "Report error",
                "detail": str(e),
                "traceback": tb,
            })

    # ---- log format: cleaner than the default ----

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] "
                         f"{self.command} {self.path} → {args[1] if len(args) > 1 else '?'}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="SAGE — Stormwater Analysis & GSI Evaluator HTTP server."
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="Port to bind (default: 8765)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host to bind (default: 127.0.0.1, localhost only)")
    args = parser.parse_args(argv)

    if not (WEB_DIR / "index.html").exists():
        print(f"Error: web/index.html not found at {WEB_DIR}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), SageHandler)
    print(f"SAGE v{__version__} server running at http://{args.host}:{args.port}/")
    print(f"  by Druid (https://druid.solutions) — Apache 2.0 licensed")
    print(f"  Open the URL above in a web browser. Press Ctrl+C to stop.")
    print()
    print(f"  Reminder: generated results must be independently verified")
    print(f"  by the user or responsible professional. Use of this software")
    print(f"  does not constitute agency approval or guarantee of compliance.")
    print()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
