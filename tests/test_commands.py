"""
End-to-end CLI tests.

Every test:
  - runs gigaflow as a real subprocess (exercising the installed entry point)
  - points --backend at the in-process mock HTTP server (no real DB / OpenAI)
  - uses an isolated HOME so ~/.gigaflow/config.json is never touched
"""

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from _constants import GIGAFLOW, MOCK_DATASOURCE_ID, MOCK_PROJECT_ID, MOCK_TRACE_ID
from conftest import _MockAPIHandler


# ── helpers ───────────────────────────────────────────────────────────────────

def run(args: list[str], env: dict, stdin: bytes = b"", timeout: int = 15) -> subprocess.CompletedProcess:
    """
    Run the gigaflow CLI as a subprocess.

    preexec_fn=os.setsid detaches the child from the parent's controlling tty,
    forcing getpass to fall back to stdin instead of /dev/tty.
    """
    return subprocess.run(
        GIGAFLOW + args,
        input=stdin,
        capture_output=True,
        text=False,
        timeout=timeout,
        env=env,
        preexec_fn=os.setsid,
    )


def out(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode(errors="replace")


def err(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode(errors="replace")


# ── installation smoke test ───────────────────────────────────────────────────

class TestInstall:
    def test_entry_point_exists(self, installed_cli):
        """The gigaflow script must be present after pip install."""
        assert Path(GIGAFLOW[0]).exists(), f"entry point not found: {GIGAFLOW[0]}"

    def test_help(self, installed_cli, clean_env):
        result = run(["--help"], clean_env)
        assert result.returncode == 0
        assert b"gigaflow" in result.stdout

    def test_no_args_prints_help(self, installed_cli, clean_env):
        result = run([], clean_env)
        assert result.returncode == 0
        assert b"usage" in result.stdout.lower() or b"gigaflow" in result.stdout


# ── gigaflow setup ────────────────────────────────────────────────────────────

class TestSetup:
    def test_already_configured(self, installed_cli, mock_server, configured_env):
        """When a config already exists setup should skip the wizard."""
        result = run(["--backend", mock_server, "setup"], configured_env)
        assert result.returncode == 0
        assert b"Already configured" in result.stdout

    def test_fresh_setup_wizard(self, installed_cli, mock_server, clean_env, tmp_path):
        """
        Full wizard run: pipe answers through stdin.

        Prompts in order (see _setup.run_wizard):
          1. Path to gigaflow.env  (blank → manual entry)
          2. Project name          [arize-phoenix-project]
          3. Path to transform     (blank → built-in)
          4. Host                  [host.docker.internal]
          5. Port                  (required, no default)
          6. User                  [postgres]
          7. Password              (via getpass — reads stdin because setsid removes tty)
          8. Database              [postgres]
          9. Source table          [spans]
        """
        stdin = b"\n\n\n\n5432\n\ntestpass\n\n\n"
        result = run(["--backend", mock_server, "setup"], clean_env, stdin=stdin)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Project created" in output
        assert "Transform config uploaded" in output
        assert "Datasource registered" in output
        assert "Sync complete" in output
        assert "Configuration saved" in output
        assert "built-in Arize Phoenix" in output

        # config file must have been written to the isolated HOME
        home = Path(clean_env["HOME"])
        cfg_path = home / ".gigaflow" / "config.json"
        assert cfg_path.exists()
        cfg = json.loads(cfg_path.read_text())
        assert cfg["project_id"] == MOCK_PROJECT_ID
        assert cfg["datasource_id"] == MOCK_DATASOURCE_ID


    def test_setup_wizard_custom_transform(self, installed_cli, mock_server, clean_env, tmp_path):
        """Providing a path to an existing transform file uploads that file."""
        transform_file = tmp_path / "custom_transform.yml"
        transform_file.write_text("version: '1.0'\nsource: custom\nprimitives: {}\n")

        path_input = str(transform_file).encode() + b"\n"
        # 1. env file (blank), 2. project name, 3. transform path, 4. host, 5. port, 6. user, 7. password, 8. db, 9. table
        stdin = b"\n\n" + path_input + b"\n5432\n\ntestpass\n\n\n"
        result = run(["--backend", mock_server, "setup"], clean_env, stdin=stdin)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Loaded transform file" in output
        assert "Transform config uploaded" in output
        assert "Configuration saved" in output

    def test_setup_wizard_with_env_file(self, installed_cli, mock_server, clean_env, tmp_path):
        """Entering an env file path pre-fills prompts; password skips the getpass prompt."""
        env_file = tmp_path / "gigaflow.env"
        env_file.write_text(
            "OPENAI_API_KEY=sk-test-key\n"
            "GIGAFLOW_PROJECT_NAME=env-project\n"
            "GIGAFLOW_DB_HOST=host.docker.internal\n"
            "GIGAFLOW_DB_PORT=5432\n"
            "GIGAFLOW_DB_USER=postgres\n"
            "GIGAFLOW_DB_PASSWORD=secretpass\n"
            "GIGAFLOW_DB_NAME=postgres\n"
            "GIGAFLOW_DB_TABLE=spans\n"
        )
        # 1. env file path, then blanks for all remaining prompts (password skipped)
        env_path_input = str(env_file).encode() + b"\n"
        stdin = env_path_input + b"\n\n\n\n\n\n\n"
        result = run(["--backend", mock_server, "setup"], clean_env, stdin=stdin)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Loaded env file" in output
        assert "Password: [from env file]" in output
        assert "Project created" in output
        assert "Configuration saved" in output

        cfg = json.loads((Path(clean_env["HOME"]) / ".gigaflow" / "config.json").read_text())
        assert "openai_api_key" not in cfg

    def test_setup_wizard_bad_env_file_path(self, installed_cli, mock_server, clean_env, tmp_path):
        """A bad env file path prints an error but the wizard continues with manual entry."""
        missing = str(tmp_path / "nonexistent.env").encode() + b"\n"
        # After bad env path: project name, transform, host, port, user, password, db, table
        stdin = missing + b"\n\n\n5432\n\ntestpass\n\n\n"
        result = run(["--backend", mock_server, "setup"], clean_env, stdin=stdin)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Could not read env file" in output or "Could not read env file" in err(result)
        assert "Configuration saved" in output

    def test_setup_wizard_bad_transform_path(self, installed_cli, mock_server, clean_env, tmp_path):
        """Providing a path to a non-existent file aborts setup with an error."""
        missing = str(tmp_path / "nonexistent.yml").encode() + b"\n"
        stdin = b"\n\n" + missing + b"\n5432\n\ntestpass\n\n\n"
        result = run(["--backend", mock_server, "setup"], clean_env, stdin=stdin)
        assert result.returncode != 0
        assert b"Could not read transform file" in result.stderr


# ── gigaflow sync ─────────────────────────────────────────────────────────────

class TestSync:
    def test_sync(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "sync"], configured_env)
        assert result.returncode == 0, err(result)
        assert b"Sync complete" in result.stdout
        assert b"2 trace(s)" in result.stdout

    def test_sync_no_config(self, installed_cli, mock_server, clean_env):
        """Without a config sync should fail, not crash."""
        result = run(["--backend", mock_server, "sync"], clean_env)
        assert result.returncode != 0
        assert b"gigaflow setup" in result.stderr or b"gigaflow setup" in result.stdout


# ── gigaflow traces ───────────────────────────────────────────────────────────

class TestTraces:
    def test_traces(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "traces"], configured_env)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert MOCK_TRACE_ID in output
        assert "test-trace" in output
        assert "1 trace(s)" in output

    def test_traces_no_sync(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "traces", "--no-sync"], configured_env)
        assert result.returncode == 0, err(result)
        # --no-sync skips the syncing section
        assert b"Syncing" not in result.stdout
        assert MOCK_TRACE_ID.encode() in result.stdout


# ── gigaflow spans ────────────────────────────────────────────────────────────

class TestSpans:
    def test_spans(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "spans", MOCK_TRACE_ID], configured_env)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "chat-completion" in output
        assert "web-search" in output
        assert "llm_call" in output
        assert "tool_invocation" in output
        assert "2 span(s)" in output

    def test_spans_no_sync(self, installed_cli, mock_server, configured_env):
        result = run(
            ["--backend", mock_server, "spans", MOCK_TRACE_ID, "--no-sync"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        assert b"Syncing" not in result.stdout

    def test_spans_bad_trace_id(self, installed_cli, mock_server, configured_env):
        """A trace ID the mock server doesn't know should return an error."""
        result = run(
            ["--backend", mock_server, "spans", "00000000-dead-beef-0000-000000000000"],
            configured_env,
        )
        assert result.returncode != 0


# ── gigaflow run aif ──────────────────────────────────────────────────────────

class TestRunAif:
    def test_select_by_number(self, installed_cli, mock_server, configured_env_with_key):
        """Entering '1' selects the first trace and prints the viewer URL."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env_with_key,
            stdin=b"1\n",
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert MOCK_TRACE_ID[:8] in output
        assert "Opening viewer" in output
        assert MOCK_TRACE_ID in output

    def test_select_by_full_id(self, installed_cli, mock_server, configured_env_with_key):
        """Pasting a full trace UUID works as input."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env_with_key,
            stdin=f"{MOCK_TRACE_ID}\n".encode(),
        )
        assert result.returncode == 0, err(result)
        assert MOCK_TRACE_ID in out(result)

    def test_lists_traces_before_prompt(self, installed_cli, mock_server, configured_env_with_key):
        """The trace list is printed so the user can pick."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env_with_key,
            stdin=b"1\n",
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Available Traces" in output
        assert "test-trace" in output

    def test_invalid_number_exits_nonzero(self, installed_cli, mock_server, configured_env):
        """Entering a number out of range exits with an error."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env,
            stdin=b"99\n",
        )
        assert result.returncode != 0

    def test_empty_input_exits_cleanly(self, installed_cli, mock_server, configured_env):
        """Pressing Enter with no input exits with code 0 (user cancelled)."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env,
            stdin=b"\n",
        )
        assert result.returncode == 0

    def test_eof_exits_cleanly(self, installed_cli, mock_server, configured_env):
        """Ctrl-D (EOF) exits with code 0."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env,
            stdin=b"",
        )
        assert result.returncode == 0

    def test_no_subcommand_prints_help(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "run"], configured_env)
        assert b"Traceback" not in result.stderr

    def test_run_aif_no_config_triggers_setup(self, installed_cli, mock_server, clean_env):
        """Without a config the wizard is triggered (we just verify it doesn't crash with a traceback)."""
        # Feed EOF immediately so the wizard exits gracefully
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-browser"],
            clean_env,
            stdin=b"\n\n\n\n5432\n\ntestpass\n\n\n1\n",
        )
        assert b"Traceback" not in result.stderr

    def test_aif_forwards_api_key_from_env(self, installed_cli, mock_server, configured_env_with_key):
        """OPENAI_API_KEY env var must be sent as api_key in the AIF POST body."""
        _MockAPIHandler.last_aif_body = {}
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env_with_key,
            stdin=b"1\n",
        )
        assert result.returncode == 0, err(result)
        assert _MockAPIHandler.last_aif_body.get("api_key") == "sk-test-key-from-env"

    def test_aif_forwards_api_key_from_env_file(self, installed_cli, mock_server, configured_env, tmp_path):
        """OPENAI_API_KEY from a gigaflow.env file passed via --env-file is forwarded."""
        _MockAPIHandler.last_aif_body = {}
        env_file = tmp_path / "gigaflow.env"
        env_file.write_text("OPENAI_API_KEY=sk-test-key-from-file\n")
        result = run(
            ["--backend", mock_server, "--env-file", str(env_file),
             "run", "aif", "--no-sync", "--no-browser"],
            configured_env,
            stdin=b"1\n",
        )
        assert result.returncode == 0, err(result)
        assert _MockAPIHandler.last_aif_body.get("api_key") == "sk-test-key-from-file"

    def test_aif_exits_with_error_when_no_api_key(self, installed_cli, mock_server, configured_env):
        """When OPENAI_API_KEY is not set anywhere, exit nonzero with a clear message."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env,
            stdin=b"1\n",
        )
        assert result.returncode != 0
        assert b"No OpenAI API key" in result.stdout or b"No OpenAI API key" in result.stderr

    def test_aif_shows_analysis_progress(self, installed_cli, mock_server, configured_env_with_key):
        """Output must include AIF progress messages from the CLI."""
        result = run(
            ["--backend", mock_server, "run", "aif", "--no-sync", "--no-browser"],
            configured_env_with_key,
            stdin=b"1\n",
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Running AIF analysis" in output
        assert "AIF analysis complete" in output

    # ── direct trace_id argument ──────────────────────────────────────────────

    def test_direct_trace_id_skips_listing(self, installed_cli, mock_server, configured_env_with_key):
        """Passing TRACE_ID directly should skip the interactive trace listing."""
        result = run(
            ["--backend", mock_server, "run", "aif", MOCK_TRACE_ID, "--no-browser"],
            configured_env_with_key,
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Available Traces" not in output
        assert "AIF analysis complete" in output
        assert MOCK_TRACE_ID in output

    def test_direct_trace_id_skips_sync(self, installed_cli, mock_server, configured_env_with_key):
        """When TRACE_ID is given directly, auto-sync should be skipped."""
        result = run(
            ["--backend", mock_server, "run", "aif", MOCK_TRACE_ID, "--no-browser"],
            configured_env_with_key,
        )
        assert result.returncode == 0, err(result)
        assert b"Syncing" not in result.stdout

    def test_direct_trace_id_no_browser(self, installed_cli, mock_server, configured_env_with_key):
        """--no-browser suppresses webbrowser.open when trace_id is given directly."""
        result = run(
            ["--backend", mock_server, "run", "aif", MOCK_TRACE_ID, "--no-browser"],
            configured_env_with_key,
        )
        assert result.returncode == 0, err(result)
        assert "Opening viewer" in out(result)

    def test_direct_trace_id_requires_api_key(self, installed_cli, mock_server, configured_env):
        """Missing API key should still fail even when TRACE_ID is given directly."""
        result = run(
            ["--backend", mock_server, "run", "aif", MOCK_TRACE_ID, "--no-browser"],
            configured_env,
        )
        assert result.returncode != 0
        assert b"No OpenAI API key" in result.stdout or b"No OpenAI API key" in result.stderr


# ── gigaflow projects / datasources ──────────────────────────────────────────

class TestProjects:
    def test_projects(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "projects"], configured_env)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "test-project" in output
        assert "1 project(s)" in output

    def test_datasources(self, installed_cli, mock_server, configured_env):
        result = run(["--backend", mock_server, "datasources"], configured_env)
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "arize-phoenix" in output
        assert "1 datasource(s)" in output


# ── gigaflow config ───────────────────────────────────────────────────────────

class TestConfig:
    def test_config_show(self, installed_cli, mock_server, configured_env):
        result = run(["config", "show"], configured_env)
        assert result.returncode == 0, err(result)
        output = out(result)
        # output should be valid JSON containing our IDs
        parsed = json.loads(output.strip())
        assert parsed["project_id"] == MOCK_PROJECT_ID
        assert parsed["datasource_id"] == MOCK_DATASOURCE_ID

    def test_config_show_no_config(self, installed_cli, clean_env):
        result = run(["config", "show"], clean_env)
        assert result.returncode == 0
        assert b"No configuration found" in result.stdout

    def test_config_clear(self, installed_cli, mock_server, configured_env, tmp_path):
        cfg_path = Path(configured_env["HOME"]) / ".gigaflow" / "config.json"
        assert cfg_path.exists()

        result = run(["config", "clear"], configured_env)
        assert result.returncode == 0, err(result)
        assert b"cleared" in result.stdout
        assert not cfg_path.exists()

    def test_config_no_subcommand(self, installed_cli, clean_env):
        result = run(["config"], clean_env)
        assert b"Traceback" not in result.stderr


# ── gigaflow inspect ──────────────────────────────────────────────────────────

class TestInspect:
    def test_inspect_cli_tree(self, installed_cli, mock_server, configured_env):
        result = run(
            ["--backend", mock_server, "inspect", MOCK_TRACE_ID, "--cli"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Trace Metadata" in output
        assert "Span Tree" in output
        assert "Orchestration Flow" in output
        assert "chat-completion" in output
        assert "web-search" in output

    def test_inspect_cli_shows_classification_summary(self, installed_cli, mock_server, configured_env):
        result = run(
            ["--backend", mock_server, "inspect", MOCK_TRACE_ID, "--cli"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "Classification Summary" in output
        assert "llm_call" in output
        assert "tool_invocation" in output

    def test_inspect_cli_shows_orchestration_flow(self, installed_cli, mock_server, configured_env):
        result = run(
            ["--backend", mock_server, "inspect", MOCK_TRACE_ID, "--cli"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        # tool call and LLM answer should appear in flow section
        assert "search" in output
        assert "gpt-4o-mini" in output

    def test_inspect_web_serves_html(self, installed_cli, mock_server, configured_env):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        proc = subprocess.Popen(
            GIGAFLOW + ["--backend", mock_server, "inspect", MOCK_TRACE_ID,
                        "--no-browser", "--port", str(port)],
            env=configured_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(1.0)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=5) as r:
                html = r.read().decode()
            assert "GigaFlow Trace Inspector" in html
            assert MOCK_TRACE_ID in html
            assert "chat-completion" in html
        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_inspect_web_server_rendered_content(self, installed_cli, mock_server, configured_env):
        """HTML is server-rendered — all content must be present without JS execution."""
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        proc = subprocess.Popen(
            GIGAFLOW + ["--backend", mock_server, "inspect", MOCK_TRACE_ID,
                        "--no-browser", "--port", str(port)],
            env=configured_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(1.0)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=5) as r:
                html = r.read().decode()

            # trace metadata is in the page
            assert MOCK_TRACE_ID in html
            assert "test-trace" in html
            assert "completed" in html

            # primitive classification stats
            assert "user_input" in html
            assert "tool_invocation" in html
            assert "llm_call" in html

            # orchestration flow section
            assert "USER INPUT" in html
            assert "TOOL CALLS" in html
            assert "LLM ANSWERS" in html

            # span data embedded directly (no JS needed)
            assert "chat-completion" in html
            assert "web-search" in html
            assert "gpt-4o-mini" in html
            assert "search" in html  # tool_name

            # expandable details elements (no JS required for basic interaction)
            assert "<details>" in html
            assert "<summary>" in html

        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_inspect_web_span_primitive_data_embedded(self, installed_cli, mock_server, configured_env):
        """primitive_data fields appear directly in the HTML."""
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        proc = subprocess.Popen(
            GIGAFLOW + ["--backend", mock_server, "inspect", MOCK_TRACE_ID,
                        "--no-browser", "--port", str(port)],
            env=configured_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(1.0)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=5) as r:
                html = r.read().decode()
            # model and tool_name from primitive_data are rendered server-side
            assert "gpt-4o-mini" in html
            assert "tool_name" in html
            assert "prompt_tokens" in html
        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_inspect_bad_trace_id(self, installed_cli, mock_server, configured_env):
        result = run(
            ["--backend", mock_server, "inspect",
             "00000000-dead-beef-0000-000000000000", "--cli"],
            configured_env,
        )
        assert result.returncode != 0


# ── backend unreachable ───────────────────────────────────────────────────────

class TestBackendDown:
    def test_sync_backend_down(self, installed_cli, configured_env):
        """CLI should print a clean error, not a traceback, when backend is unreachable."""
        result = run(
            ["--backend", "http://127.0.0.1:19999/api/v1", "sync"],
            configured_env,
        )
        assert result.returncode != 0
        assert b"Traceback" not in result.stderr

    def test_traces_backend_down(self, installed_cli, configured_env):
        result = run(
            ["--backend", "http://127.0.0.1:19999/api/v1", "traces", "--no-sync"],
            configured_env,
        )
        assert result.returncode != 0
        assert b"Traceback" not in result.stderr
