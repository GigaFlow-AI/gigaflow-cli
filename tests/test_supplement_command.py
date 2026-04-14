"""End-to-end tests for the ``gigaflow supplement`` subcommand.

Uses the shared mock API server (see conftest.py) to capture the POST
body the CLI uploads. Verifies path resolution (--latest, --session-file,
explicit SESSION_ID under a fake ~/.claude/projects hierarchy) and the
gzip content-encoding the supplement endpoint expects.
"""

import gzip
from pathlib import Path

from conftest import _MockAPIHandler
from test_commands import err, out, run


def _make_fake_claude_projects(home: Path) -> Path:
    """Create a fake ~/.claude/projects/<slug>/<session>.jsonl under the test home."""
    projects = home / ".claude" / "projects" / "-home-user-proj"
    projects.mkdir(parents=True, exist_ok=True)
    session_file = projects / "11111111-1111-1111-1111-111111111111.jsonl"
    session_file.write_text(
        '{"type":"user","promptId":"p1","message":{"role":"user","content":"hi"},'
        '"sessionId":"11111111-1111-1111-1111-111111111111","timestamp":"2026-04-09T10:00:00Z"}\n'
    )
    return session_file


class TestSupplementCommand:
    def test_supplement_by_session_id_auto_locates_file(
        self, installed_cli, mock_server, configured_env, tmp_path
    ):
        _make_fake_claude_projects(Path(configured_env["HOME"]))
        _MockAPIHandler.last_supplement_body = b""
        _MockAPIHandler.last_supplement_query = ""

        result = run(
            ["--backend", mock_server, "supplement", "11111111-1111-1111-1111-111111111111"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        output = out(result)
        assert "spans_matched" in output
        assert "5" in output
        # Server received a gzip body
        assert _MockAPIHandler.last_supplement_body
        decompressed = gzip.decompress(_MockAPIHandler.last_supplement_body)
        assert b"promptId" in decompressed
        # Session id query param present
        assert "session_id=11111111-1111-1111-1111-111111111111" in _MockAPIHandler.last_supplement_query

    def test_supplement_latest_picks_most_recent(
        self, installed_cli, mock_server, configured_env
    ):
        home = Path(configured_env["HOME"])
        _make_fake_claude_projects(home)
        _MockAPIHandler.last_supplement_body = b""

        result = run(["--backend", mock_server, "supplement", "--latest"], configured_env)
        assert result.returncode == 0, err(result)
        assert "spans_matched" in out(result)
        assert _MockAPIHandler.last_supplement_body

    def test_supplement_session_file_override(
        self, installed_cli, mock_server, configured_env, tmp_path
    ):
        custom = tmp_path / "custom-session.jsonl"
        custom.write_text('{"type":"user","promptId":"p9","sessionId":"sfoo","message":{}}\n')
        _MockAPIHandler.last_supplement_body = b""

        result = run(
            [
                "--backend", mock_server,
                "supplement", "--session-file", str(custom), "manual-session-id",
            ],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        assert _MockAPIHandler.last_supplement_body

    def test_supplement_dry_run_query_flag(
        self, installed_cli, mock_server, configured_env
    ):
        _make_fake_claude_projects(Path(configured_env["HOME"]))
        _MockAPIHandler.last_supplement_query = ""

        result = run(
            ["--backend", mock_server, "supplement", "--latest", "--dry-run"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        assert "dry_run=true" in _MockAPIHandler.last_supplement_query

    def test_supplement_with_subagents_query_flag(
        self, installed_cli, mock_server, configured_env
    ):
        _make_fake_claude_projects(Path(configured_env["HOME"]))
        _MockAPIHandler.last_supplement_query = ""

        result = run(
            ["--backend", mock_server, "supplement", "--latest", "--with-subagents"],
            configured_env,
        )
        assert result.returncode == 0, err(result)
        assert "include_subagents=true" in _MockAPIHandler.last_supplement_query

    def test_supplement_session_not_found(
        self, installed_cli, mock_server, configured_env
    ):
        result = run(
            ["--backend", mock_server, "supplement", "ffffffff-ffff-ffff-ffff-ffffffffffff"],
            configured_env,
        )
        assert result.returncode != 0
        assert b"not found" in result.stderr.lower() or b"not found" in result.stdout.lower()

    def test_supplement_requires_some_selector(
        self, installed_cli, mock_server, configured_env
    ):
        result = run(["--backend", mock_server, "supplement"], configured_env)
        assert result.returncode != 0

    def test_supplement_help(self, installed_cli, clean_env):
        result = run(["supplement", "--help"], clean_env)
        assert result.returncode == 0
        assert b"supplement" in result.stdout.lower()
        assert b"--latest" in result.stdout
