"""Tests for hosted-backend support: _http auth/timeout/retry, _config api_key,
and the URL/key resolution contract.

The in-process tests spin up a tiny stdlib HTTP server that records the headers
it receives, so we can assert the CLI's HTTP layer forwards
"Authorization: Bearer <key>" and merges extra headers. The subprocess tests at
the bottom reuse the shared mock server / installed-CLI harness from conftest.py.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from _constants import MOCK_TRACE_ID  # noqa: F401  (kept for parity / future use)
from conftest import _MockAPIHandler

from gigaflow import _config, _http


def _run_cli(args, env_extra=None):
    """Run the CLI via `python -m gigaflow.cli` with an isolated HOME.

    Uses the module entry point so the test works against the editable checkout
    without depending on the installed console script. preexec_fn=os.setsid
    detaches the controlling tty so interactive prompts (if any) read stdin.
    """
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("GIGAFLOW_API_KEY", None)
    env.pop("GIGAFLOW_BACKEND_URL", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "gigaflow.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )


# ── a header-recording mock backend ──────────────────────────────────────────


class _RecordingHandler(BaseHTTPRequestHandler):
    received_headers: dict = {}
    received_bodies: list = []
    fail_status = None  # when set, respond with this status code

    def log_message(self, *args):  # silence
        pass

    def _read_body(self) -> bytes:
        raw = self.headers.get("Content-Length") or "0"
        try:
            n = int(raw)
        except ValueError:
            n = 0
        return self.rfile.read(n) if n else b""

    def _respond(self):
        body = self._read_body()
        _RecordingHandler.received_headers = dict(self.headers.items())
        _RecordingHandler.received_bodies.append(body)
        status = _RecordingHandler.fail_status or 200
        payload = json.dumps({"ok": status == 200}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def do_PUT(self):
        self._respond()


@pytest.fixture
def recording_backend():
    _RecordingHandler.received_headers = {}
    _RecordingHandler.received_bodies = []
    _RecordingHandler.fail_status = None
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/api/v1", _RecordingHandler
    server.shutdown()


# ── _http.api auth / headers ─────────────────────────────────────────────────


def test_api_sends_bearer_token(recording_backend):
    base_url, handler = recording_backend
    status, _ = _http.api(base_url, "GET", "/health", api_key="secret-key")
    assert status == 200
    assert handler.received_headers.get("Authorization") == "Bearer secret-key"


def test_api_omits_auth_header_without_key(recording_backend):
    base_url, handler = recording_backend
    _http.api(base_url, "GET", "/health")
    assert "Authorization" not in handler.received_headers


def test_api_merges_extra_headers(recording_backend):
    base_url, handler = recording_backend
    _http.api(base_url, "GET", "/health", headers={"X-Custom": "yes"})
    assert handler.received_headers.get("X-Custom") == "yes"


def test_api_post_sends_bearer_and_body(recording_backend):
    base_url, handler = recording_backend
    status, _ = _http.api(
        base_url, "POST", "/aif/abc", {"api_key": "sk-openai"}, api_key="gf-key"
    )
    assert status == 200
    # gigaflow key in header; OpenAI key in body — kept separate.
    assert handler.received_headers.get("Authorization") == "Bearer gf-key"
    assert b"sk-openai" in handler.received_bodies[-1]


# ── _http.api status contract ────────────────────────────────────────────────


def test_api_returns_none_on_unreachable():
    # Non-routable TEST-NET-1 address (RFC 5737). Contract: status is None.
    status, payload = _http.api("http://192.0.2.1:8000", "GET", "/health", timeout=2)
    assert status is None
    assert "error" in payload


def test_api_returns_http_error_status(recording_backend):
    base_url, handler = recording_backend
    handler.fail_status = 403
    status, _ = _http.api(base_url, "GET", "/health", api_key="bad")
    assert status == 403


def test_api_retries_idempotent_get(monkeypatch):
    # Connection failure triggers retries; assert sleep is called between tries.
    sleeps: list = []
    monkeypatch.setattr(_http.time, "sleep", lambda s: sleeps.append(s))
    status, _ = _http.api("http://192.0.2.1:8000", "GET", "/health", timeout=2)
    assert status is None
    # _MAX_TRIES attempts → (_MAX_TRIES - 1) backoff sleeps.
    assert len(sleeps) == _http._MAX_TRIES - 1


def test_api_does_not_retry_post(monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(_http.time, "sleep", lambda s: sleeps.append(s))
    status, _ = _http.api("http://192.0.2.1:8000", "POST", "/aif/x", {"a": 1}, timeout=2)
    assert status is None
    assert sleeps == []  # non-idempotent → no retry


# ── friendly error hints ─────────────────────────────────────────────────────


def test_unreachable_hint_mentions_url_and_env():
    msg = _http.unreachable_hint("http://example/api/v1")
    assert "http://example/api/v1" in msg
    assert "GIGAFLOW_BACKEND_URL" in msg


def test_auth_error_hint_is_actionable():
    msg = _http.auth_error_hint()
    assert "GIGAFLOW_API_KEY" in msg
    assert "setup" in msg


# ── _config api_key round-trip ───────────────────────────────────────────────


def test_config_set_get_api_key(tmp_path, monkeypatch):
    cfg_path = tmp_path / ".gigaflow" / "config.json"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)

    assert _config.get("api_key") is None
    _config.set("backend_url", "https://api.example/api/v1")
    _config.set("api_key", "gf_live_123")

    assert _config.get("api_key") == "gf_live_123"
    # set() preserves other keys.
    assert _config.get("backend_url") == "https://api.example/api/v1"

    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["api_key"] == "gf_live_123"
    assert on_disk["backend_url"] == "https://api.example/api/v1"


def test_config_clear_removes_api_key(tmp_path, monkeypatch):
    cfg_path = tmp_path / ".gigaflow" / "config.json"
    monkeypatch.setattr(_config, "CONFIG_PATH", cfg_path)
    _config.set("api_key", "gf_live_123")
    _config.clear()
    assert _config.get("api_key") is None
    assert not cfg_path.exists()


# ── end-to-end CLI auth + resolution (subprocess) ─────────────────────────────


def test_compute_forwards_gigaflow_key_via_flag(installed_cli, mock_server, tmp_path):
    _MockAPIHandler.last_auth_header = None
    _MockAPIHandler.last_aif_body = {}
    result = _run_cli(
        ["--backend", mock_server, "--api-key", "gf-flag", "compute",
         "SELECT trace_id FROM trace_metrics"],
        env_extra={"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    # gigaflow key in the Authorization header on /aif/...
    assert _MockAPIHandler.last_auth_header == "Bearer gf-flag"
    # OpenAI key stays in the body, separate from the auth header.
    assert _MockAPIHandler.last_aif_body.get("api_key") == "sk-openai"


def test_compute_forwards_gigaflow_key_via_env(installed_cli, mock_server, tmp_path):
    _MockAPIHandler.last_auth_header = None
    result = _run_cli(
        ["compute", "SELECT trace_id FROM trace_metrics"],
        env_extra={
            "HOME": str(tmp_path),
            "OPENAI_API_KEY": "sk-openai",
            "GIGAFLOW_BACKEND_URL": mock_server,
            "GIGAFLOW_API_KEY": "gf-env",
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _MockAPIHandler.last_auth_header == "Bearer gf-env"


def test_projects_resolves_backend_from_env(installed_cli, mock_server, tmp_path):
    # No --backend flag; resolve from $GIGAFLOW_BACKEND_URL.
    result = _run_cli(
        ["projects"],
        env_extra={"HOME": str(tmp_path), "GIGAFLOW_BACKEND_URL": mock_server},
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_backend_flag_overrides_env(installed_cli, mock_server, tmp_path):
    # Env points at a non-routable host; --backend flag should win.
    result = _run_cli(
        ["--backend", mock_server, "projects"],
        env_extra={
            "HOME": str(tmp_path),
            "GIGAFLOW_BACKEND_URL": "http://192.0.2.1:8000/api/v1",
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_unreachable_backend_prints_friendly_message(installed_cli, tmp_path):
    # TEST-NET-1 (RFC 5737) is guaranteed non-routable — fails fast and
    # deterministically, unlike an ephemeral localhost port.
    result = _run_cli(
        ["--backend", "http://192.0.2.1:8000/api/v1", "projects"],
        env_extra={"HOME": str(tmp_path)},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "Could not reach" in combined, combined
    assert "Traceback" not in combined


def test_localhost_default_when_nothing_set(installed_cli, tmp_path):
    """With no --backend, no env, no config → defaults to localhost:8000.

    Nothing is listening there in the test environment, so the command fails
    with the friendly unreachable message that names the default URL — proving
    the localhost default was the URL actually used.
    """
    result = _run_cli(
        ["projects"],
        env_extra={"HOME": str(tmp_path)},  # GIGAFLOW_BACKEND_URL is stripped by _run_cli
    )
    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "localhost:8000" in combined, combined
    assert "Traceback" not in combined


def test_config_api_key_used_when_no_flag_or_env(installed_cli, mock_server, tmp_path):
    """api_key resolution falls back to the saved config when no flag/env is set."""
    cfg_dir = tmp_path / ".gigaflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "backend_url": mock_server,
        "api_key": "gf-from-config",
    }))
    _MockAPIHandler.last_auth_header = None
    result = _run_cli(
        ["compute", "SELECT trace_id FROM trace_metrics"],
        env_extra={"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai"},
        # no --api-key flag, no GIGAFLOW_API_KEY env
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _MockAPIHandler.last_auth_header == "Bearer gf-from-config"


def test_flag_api_key_overrides_config(installed_cli, mock_server, tmp_path):
    """--api-key wins over a different key saved in config."""
    cfg_dir = tmp_path / ".gigaflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "backend_url": mock_server,
        "api_key": "gf-from-config",
    }))
    _MockAPIHandler.last_auth_header = None
    result = _run_cli(
        ["--api-key", "gf-from-flag", "compute",
         "SELECT trace_id FROM trace_metrics"],
        env_extra={"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _MockAPIHandler.last_auth_header == "Bearer gf-from-flag"


def test_env_api_key_overrides_config(installed_cli, mock_server, tmp_path):
    """$GIGAFLOW_API_KEY wins over a different key saved in config."""
    cfg_dir = tmp_path / ".gigaflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "backend_url": mock_server,
        "api_key": "gf-from-config",
    }))
    _MockAPIHandler.last_auth_header = None
    result = _run_cli(
        ["compute", "SELECT trace_id FROM trace_metrics"],
        env_extra={
            "HOME": str(tmp_path),
            "OPENAI_API_KEY": "sk-openai",
            "GIGAFLOW_API_KEY": "gf-from-env",
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _MockAPIHandler.last_auth_header == "Bearer gf-from-env"


# ── friendly 401/403 messaging, end-to-end via the mock backend ───────────────
#
# The shared mock backend (conftest) replies 401 for `Bearer FORCE401` and 403
# for `Bearer FORCE403`, so the CLI's real auth-error path runs without a live
# secured server.


def test_compute_401_prints_friendly_message(installed_cli, mock_server, tmp_path):
    result = _run_cli(
        ["--backend", mock_server, "--api-key", "FORCE401", "compute",
         "SELECT trace_id FROM trace_metrics"],
        env_extra={"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    combined = (result.stdout + result.stderr)
    assert "Authentication failed" in combined, combined
    assert "Traceback" not in combined


def test_compute_403_prints_friendly_message(installed_cli, mock_server, tmp_path):
    result = _run_cli(
        ["--backend", mock_server, "--api-key", "FORCE403", "compute",
         "SELECT trace_id FROM trace_metrics"],
        env_extra={"HOME": str(tmp_path), "OPENAI_API_KEY": "sk-openai"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Authentication failed" in (result.stdout + result.stderr)
    assert "Traceback" not in (result.stdout + result.stderr)


def test_projects_401_prints_friendly_message(installed_cli, mock_server, tmp_path):
    result = _run_cli(
        ["--backend", mock_server, "--api-key", "FORCE401", "projects"],
        env_extra={"HOME": str(tmp_path)},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Authentication failed" in (result.stdout + result.stderr)
    assert "Traceback" not in (result.stdout + result.stderr)


# ── deterministic retry / backoff (unit, no real sleeps) ──────────────────────


class _FakeResp:
    status = 200

    def read(self):
        return b'{"ok": true}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_get_retries_then_succeeds(monkeypatch):
    """A GET that fails once on a connection error retries and returns success."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("transient")
        return _FakeResp()

    monkeypatch.setattr(_http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_http.time, "sleep", lambda *_: None)  # no real waiting

    status, payload = _http.api("http://x/api/v1", "GET", "/health")
    assert status == 200
    assert payload == {"ok": True}
    assert calls["n"] == 2  # failed once, retried once, succeeded


def test_get_gives_up_after_max_tries(monkeypatch):
    """A GET against a permanently-down backend stops after exactly _MAX_TRIES."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr(_http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_http.time, "sleep", lambda *_: None)

    status, _ = _http.api("http://x/api/v1", "GET", "/health")
    assert status is None
    assert calls["n"] == _http._MAX_TRIES  # bounded — no infinite loop


def test_post_not_retried_on_connection_error(monkeypatch):
    """Non-idempotent POSTs are attempted exactly once on a connection error."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr(_http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_http.time, "sleep", lambda *_: None)

    status, _ = _http.api("http://x/api/v1", "POST", "/aif/x", {"a": 1})
    assert status is None
    assert calls["n"] == 1  # POST is never retried


def test_http_error_status_not_retried(monkeypatch):
    """A 4xx HTTP response is returned immediately even on a GET (auth fails fast)."""
    import io

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            "http://x/api/v1/health", 401, "Unauthorized", {},
            io.BytesIO(b'{"detail": "no"}'),
        )

    monkeypatch.setattr(_http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_http.time, "sleep", lambda *_: None)

    status, _ = _http.api("http://x/api/v1", "GET", "/health", api_key="bad")
    assert status == 401
    assert calls["n"] == 1  # not retried despite being a GET


# ── sync honors the resolved api key (regression: dropped for sync/auto-sync) ──


def test_handle_sync_forwards_resolved_api_key(monkeypatch):
    """`gigaflow sync` must thread the resolved key (flag/env/config) into do_sync."""
    from gigaflow.commands import setup as setup_cmd

    captured = {}

    def fake_do_sync(base_url, datasource_id, api_key=None):
        captured["api_key"] = api_key
        return (0, 0)

    monkeypatch.setattr(setup_cmd, "do_sync", fake_do_sync)
    monkeypatch.setattr(setup_cmd._config, "load", lambda: {"datasource_id": "ds1"})

    args = type("Args", (), {"api_key": "gf_resolved"})()
    setup_cmd._handle_sync(args, "http://x/api/v1")

    assert captured["api_key"] == "gf_resolved"


def test_ensure_ready_forwards_resolved_api_key(monkeypatch):
    """Auto-sync in `gigaflow traces`/`spans` must also forward the resolved key."""
    from gigaflow.commands import traces as traces_cmd

    captured = {}

    def fake_do_sync(base_url, datasource_id, api_key=None):
        captured["api_key"] = api_key
        return (0, 0)

    monkeypatch.setattr(traces_cmd, "do_sync", fake_do_sync)
    monkeypatch.setattr(traces_cmd._config, "load", lambda: {"datasource_id": "ds1"})

    traces_cmd._ensure_ready("http://x/api/v1", auto_sync=True, api_key="gf_resolved")

    assert captured["api_key"] == "gf_resolved"


# ── config show redacts saved secrets ─────────────────────────────────────────


def test_config_show_redacts_api_key():
    from gigaflow.commands import config as config_cmd

    cfg = {"backend_url": "https://api.example/api/v1", "api_key": "gf_live_secret"}
    redacted = config_cmd._redact(cfg)

    assert redacted["api_key"] == "****"
    assert redacted["backend_url"] == "https://api.example/api/v1"
    # original dict must not be mutated.
    assert cfg["api_key"] == "gf_live_secret"


def test_config_show_without_api_key_is_unchanged():
    from gigaflow.commands import config as config_cmd

    cfg = {"backend_url": "https://api.example/api/v1"}
    assert config_cmd._redact(cfg) == cfg
