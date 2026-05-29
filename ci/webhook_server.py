#!/usr/bin/env python3
"""
Agentic STLC Local Webhook Server
===================================
Lightweight HTTP server that receives POST callbacks from GitHub Actions
and HyperExecute, appending each event as a JSON line to an NDJSON file.
The conversational_orchestrator.py watch-events mode tails this file
rather than polling external APIs.

Design: stdlib-only (no Flask/FastAPI) so it runs on any Python 3.11+
without additional dependencies.

Usage:
    python ci/webhook_server.py --port 8765 --output-file reports/.webhook_events.ndjson

Endpoints:
    POST /callback          Receive pipeline stage event
    POST /he-callback       Receive HyperExecute job completion callback
    POST /_shutdown         Graceful shutdown
    GET  /health            Health check (returns 200 OK)

Event payload (from GitHub Actions):
    {
        "event":     "stage_complete",
        "stage":     "analyze",
        "timestamp": "2026-05-26T10:00:00Z",
        "data": {
            "total": 15,
            "passed": 12,
            "failed": 3
        }
    }

HyperExecute callback payload:
    {
        "jobId":   "uuid",
        "status":  "completed",
        "passed":  10,
        "failed":  2,
        "total":   12
    }
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# ── Server state (module-level so handler can access it) ─────────────────────

_output_file: Path = Path("reports/.webhook_events.ndjson")
_server_instance: HTTPServer | None = None
_shutdown_event = threading.Event()


# ── Event persistence ─────────────────────────────────────────────────────────

def _append_event(event: dict) -> None:
    """Append event as JSON line to the output file (thread-safe via file append mode)."""
    _output_file.parent.mkdir(parents=True, exist_ok=True)
    if "timestamp" not in event:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
    line = json.dumps(event, ensure_ascii=False)
    with _output_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"[webhook] event written: {event.get('event', '?')} @ {event.get('timestamp', '?')}")


def _normalize_he_event(payload: dict) -> dict:
    """Convert a raw HyperExecute callback payload to a pipeline stage_complete event."""
    status   = payload.get("status", "").lower()
    job_id   = payload.get("jobId", payload.get("job_id", ""))
    passed   = payload.get("passed", 0)
    failed   = payload.get("failed", 0)
    total    = payload.get("total", passed + failed)
    he_url   = f"https://hyperexecute.lambdatest.com/hyperexecute/task?jobId={job_id}" if job_id else ""

    # HE "completed" with 0 failures = passed
    is_done = status in ("completed", "passed", "failed", "error", "aborted")
    event_type = "stage_complete" if is_done else "stage_progress"

    return {
        "event":     event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "stage":   "hyperexecute_done",
            "he_url":  he_url,
            "job_id":  job_id,
            "status":  status,
            "passed":  passed,
            "failed":  failed,
            "total":   total,
        },
    }


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:
        # Suppress default access log to keep output clean
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond(200, b"OK")
        else:
            self._respond(404, b"Not Found")

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/_shutdown":
            self._respond(200, b"shutting down")
            _shutdown_event.set()
            return

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, b"invalid JSON")
            return

        if self.path == "/he-callback":
            event = _normalize_he_event(payload)
        elif self.path in ("/callback", "/webhook"):
            event = payload if "event" in payload else {
                "event": "unknown",
                "data":  payload,
            }
        else:
            self._respond(404, b"unknown endpoint")
            return

        _append_event(event)

        # If this is a pipeline_complete or pipeline_failed, trigger shutdown
        if event.get("event") in ("pipeline_complete", "pipeline_failed"):
            print(f"[webhook] terminal event received — server will shut down after response")
            threading.Timer(2.0, _shutdown_event.set).start()

        self._respond(200, b"accepted")

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Port helpers ──────────────────────────────────────────────────────────────

def _find_free_port(preferred: int) -> int:
    """Return preferred port if free, otherwise the next available port."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue
    # Last resort: OS-assigned
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Agentic STLC Webhook Server")
    p.add_argument("--port",        type=int, default=8765,
                   help="Preferred listen port (auto-increments if busy)")
    p.add_argument("--output-file", default="reports/.webhook_events.ndjson",
                   help="NDJSON file to append events to")
    p.add_argument("--host",        default="0.0.0.0",
                   help="Bind address (default: all interfaces)")
    p.add_argument("--timeout",     type=int, default=3600,
                   help="Auto-shutdown after N seconds (0=never)")
    return p.parse_args()


def main() -> None:
    global _output_file, _server_instance
    args = parse_args()

    _output_file = Path(args.output_file)
    _output_file.parent.mkdir(parents=True, exist_ok=True)

    port = _find_free_port(args.port)
    server = HTTPServer((args.host, port), _WebhookHandler)
    _server_instance = server

    print(f"[webhook] listening on http://localhost:{port}/callback", flush=True)
    print(f"[webhook] events → {_output_file}", flush=True)

    # Handle SIGINT / SIGTERM
    def _handle_signal(signum, frame):
        print(f"\n[webhook] signal {signum} — shutting down")
        _shutdown_event.set()

    signal.signal(signal.SIGINT,  _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    # Run server in a daemon thread so we can monitor _shutdown_event
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Auto-shutdown after timeout
    if args.timeout > 0:
        threading.Timer(args.timeout, _shutdown_event.set).start()

    _shutdown_event.wait()
    print("[webhook] shutting down server...")
    server.shutdown()
    print("[webhook] stopped")


if __name__ == "__main__":
    main()
