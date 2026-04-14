"""
Shared fixtures for gigaflow CLI end-to-end tests.

Strategy
--------
- Session-scoped `installed_cli`: pip installs the CLI package once per test run.
- Session-scoped `mock_server`: spins up a real (stdlib) HTTP server in a thread
  that stubs every GigaFlow API endpoint the CLI touches.
- Function-scoped `clean_env` / `configured_env`: subprocess env dicts with HOME
  pointing at a tmp dir so the real ~/.gigaflow/config.json is never touched.
- `os.setsid()` is passed as preexec_fn to CLI subprocesses so the child process
  gets its own session with no controlling tty — this forces getpass to fall back
  to stdin instead of /dev/tty, making interactive prompts pipeable.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from _constants import (
    CLI_DIR,
    MOCK_DATASOURCE_ID,
    MOCK_PROJECT_ID,
    MOCK_SPAN_ID,
    MOCK_TRACE_ID,
)

# ── package installation ──────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def installed_cli():
    """pip install -e ./cli into the active Python environment (once per run)."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(CLI_DIR)],
        check=True,
        capture_output=True,
    )


# ── mock HTTP server ──────────────────────────────────────────────────────────

class _MockAPIHandler(BaseHTTPRequestHandler):
    """Handles every endpoint the gigaflow CLI calls."""

    # Class-level store so tests can inspect what the server received.
    last_aif_body: dict = {}
    last_supplement_body: bytes = b""
    last_supplement_headers: dict = {}
    last_supplement_query: str = ""

    def log_message(self, *args):  # silence request logs during tests
        pass

    # ── helpers ───────────────────────────────────────────────────────────────

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _ok(self, body: dict | list):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _404(self):
        self.send_response(404)
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        p = self.path.split("?")[0]

        if p == "/api/v1/health":
            self._ok({"status": "ok"})

        elif p == "/api/v1/traces/":
            self._ok({"traces": [{
                "trace_id":   MOCK_TRACE_ID,
                "trace_name": "test-trace",
                "status":     "completed",
                "started_at": "2026-01-01T10:00:00",
                "project_id": MOCK_PROJECT_ID,
            }]})

        elif p == f"/api/v1/traces/{MOCK_TRACE_ID}":
            self._ok({
                "trace_id":     MOCK_TRACE_ID,
                "trace_name":   "test-trace",
                "status":       "completed",
                "started_at":   "2026-01-01T10:00:00",
                "completed_at": "2026-01-01T10:00:05",
                "duration_ms":  5000,
                "project_id":   MOCK_PROJECT_ID,
                "service_name": "test-service",
                "environment":  "test",
            })

        elif p == f"/api/v1/traces/{MOCK_TRACE_ID}/spans":
            self._ok([
                {
                    "span_id":        MOCK_SPAN_ID,
                    "span_name":      "chat-completion",
                    "span_type":      "llm",
                    "primitive_type": "llm_call",
                    "primitive_data": {"model": "gpt-4o-mini", "prompt_tokens": 120},
                    "started_at":     "2026-01-01T10:00:01",
                },
                {
                    "span_id":        "eeeeeeee-0000-0000-0000-000000000005",
                    "span_name":      "web-search",
                    "span_type":      "tool",
                    "primitive_type": "tool_invocation",
                    "primitive_data": {"tool_name": "search"},
                    "started_at":     "2026-01-01T10:00:02",
                },
            ])

        elif p == "/api/v1/projects/":
            self._ok({"projects": [{
                "project_id": MOCK_PROJECT_ID,
                "name":       "test-project",
                "created_at": "2026-01-01T00:00:00",
            }]})

        elif p == "/api/v1/datasources/":
            self._ok({"datasources": [{
                "datasource_id": MOCK_DATASOURCE_ID,
                "name":         "arize-phoenix",
                "source_table": "spans",
                "created_at":   "2026-01-01T00:00:00",
            }]})

        else:
            self._404()

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        p = self.path.split("?")[0]
        raw = self._body()

        if p == "/api/v1/projects/":
            self._ok({"project_id": MOCK_PROJECT_ID})

        elif p == "/api/v1/datasources/":
            self._ok({"datasource_id": MOCK_DATASOURCE_ID})

        elif p == f"/api/v1/datasources/{MOCK_DATASOURCE_ID}/sync":
            self._ok({"synced_traces": 2, "synced_spans": 14})

        elif p == "/api/v1/query/":
            try:
                body = json.loads(raw) if raw else {}
                sql = body.get("sql", "").lower()
            except Exception:
                sql = ""
            # Compute command: first pass selects trace_ids; second pass checks run_id IS NOT NULL
            if "trace_id" in sql and "run_id is not null" in sql:
                # Skip check — return empty (no already-computed traces by default)
                self._ok({"columns": ["trace_id"], "rows": [], "row_count": 0, "truncated": False})
            elif "trace_id" in sql:
                # Return trace_id column so compute can proceed
                self._ok({
                    "columns": ["trace_id"],
                    "rows": [[MOCK_TRACE_ID]],
                    "row_count": 1,
                    "truncated": False,
                })
            else:
                # Regular query — return named columns for query tests
                self._ok({
                    "columns": ["trace_name", "groundedness", "tool_consumption"],
                    "rows": [
                        ["benchmark-good-1", 0.91, 0.88],
                        ["benchmark-bad-1",  0.12, 0.08],
                    ],
                    "row_count": 2,
                    "truncated": False,
                })

        elif p == "/api/v1/supplement/claude_code":
            _MockAPIHandler.last_supplement_body = raw
            _MockAPIHandler.last_supplement_headers = dict(self.headers.items())
            _MockAPIHandler.last_supplement_query = self.path.split("?", 1)[1] if "?" in self.path else ""
            self._ok({
                "session_id":            "test-session",
                "session_file_path":     "/tmp/fake.jsonl",
                "spans_matched":         5,
                "spans_updated":         4,
                "spans_not_matched":     0,
                "jsonl_rows_unused":     0,
                "subagents_synthesized": 0,
                "bytes_ingested":        1234,
                "assistant_text_chars":  500,
                "thinking_chars":        42,
                "tool_output_chars":     128,
                "unmatched_prompts":     [],
                "orphan_spans":          [],
                "warnings":              [],
                "errors":                [],
                "dry_run":               "dry_run=true" in (self.path or ""),
            })

        elif p == f"/api/v1/aif/{MOCK_TRACE_ID}":
            try:
                _MockAPIHandler.last_aif_body = json.loads(raw) if raw else {}
            except Exception:
                _MockAPIHandler.last_aif_body = {}
            self._ok({
                "metrics": {
                    "groundedness":           0.85,
                    "groundedness_at_k":      0.80,
                    "tool_consumption":       0.70,
                    "tool_consumption_at_k":  0.65,
                    "tool_contribution":      {"search": 0.60},
                    "tool_contribution_at_k": {"search": 0.55},
                    "tool_usage":             {"search": 0.50},
                    "tool_usage_at_k":        {"search": 0.45},
                },
                "tool_atoms": [{
                    "atom_id":         "ta1",
                    "span_name":       "search",
                    "relevance_score": 0.90,
                    "content":         "Paris is the capital of France.",
                }],
                "response_atoms": [{
                    "atom_id":         "ra1",
                    "relevance_score": 0.85,
                    "content":         "The capital of France is Paris.",
                }],
                "attribution": {"ra1": ["ta1"]},
                "query":       "What is the capital of France?",
            })

        else:
            self._404()

    # ── PUT ───────────────────────────────────────────────────────────────────

    def do_PUT(self):
        p = self.path.split("?")[0]
        self._body()

        if p == f"/api/v1/projects/{MOCK_PROJECT_ID}/transform":
            self._ok({"transform_config": {"primitives": {
                "llm_call": {}, "tool_invocation": {}, "user_input": {},
            }}})
        else:
            self._404()


@pytest.fixture(scope="session")
def mock_server():
    """Start a mock GigaFlow API server; yield its base URL; shut it down after the session."""
    server = HTTPServer(("127.0.0.1", 0), _MockAPIHandler)
    port   = server.server_address[1]
    t      = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/api/v1"
    server.shutdown()




# ── per-test home dir helpers ─────────────────────────────────────────────────

def _base_env(home: Path) -> dict:
    """OS environment with HOME overridden and OPENAI_API_KEY stripped to isolate tests."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("OPENAI_API_KEY", None)
    return env


@pytest.fixture
def clean_env(tmp_path):
    """Env with an empty HOME — no gigaflow config present."""
    return _base_env(tmp_path)


@pytest.fixture
def configured_env(tmp_path, mock_server):
    """Env with HOME pre-populated with a valid gigaflow config, no API key."""
    cfg_dir = tmp_path / ".gigaflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "backend_url":   mock_server,
        "project_id":    MOCK_PROJECT_ID,
        "datasource_id": MOCK_DATASOURCE_ID,
    }))
    return _base_env(tmp_path)


@pytest.fixture
def configured_env_with_key(tmp_path, mock_server):
    """Env with HOME pre-populated with a valid gigaflow config and OPENAI_API_KEY set."""
    cfg_dir = tmp_path / ".gigaflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "backend_url":   mock_server,
        "project_id":    MOCK_PROJECT_ID,
        "datasource_id": MOCK_DATASOURCE_ID,
    }))
    env = _base_env(tmp_path)
    env["OPENAI_API_KEY"] = "sk-test-key-from-env"
    return env
