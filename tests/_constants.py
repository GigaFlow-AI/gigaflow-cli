"""Shared test constants (imported by both conftest.py and test modules)."""
import sys
from pathlib import Path

MOCK_PROJECT_ID    = "aaaaaaaa-0000-0000-0000-000000000001"
MOCK_DATASOURCE_ID = "bbbbbbbb-0000-0000-0000-000000000002"
MOCK_TRACE_ID      = "cccccccc-0000-0000-0000-000000000003"
MOCK_SPAN_ID       = "dddddddd-0000-0000-0000-000000000004"

CLI_DIR  = Path(__file__).parents[1]
GIGAFLOW = [str(Path(sys.executable).parent / "gigaflow")]
