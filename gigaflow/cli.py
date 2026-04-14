"""
gigaflow — CLI entry point.

Commands:
  gigaflow setup                     Configure GigaFlow with an Arize Phoenix datasource
  gigaflow sync                      Re-sync traces from the configured datasource
  gigaflow ui                        Open the traces dashboard in the browser
  gigaflow traces                    List all traces (auto-syncs first)
  gigaflow spans <trace_id>          List spans for a trace
  gigaflow query "<SQL>"             Run SQL SELECT against the trace_metrics view
  gigaflow compute "<SQL>"           Batch-compute AIF for traces matching a SQL query
  gigaflow supplement [SESSION_ID]   Enrich Claude Code spans with local JSONL content
  gigaflow inspect <trace_id>        Visualize a single trace (opens AIF viewer)
  gigaflow projects                  List all projects
  gigaflow config show               Show saved configuration
  gigaflow config clear              Clear saved configuration
"""

import argparse
import os
import sys

from gigaflow import _config
from gigaflow._setup import load_env_file
from gigaflow.commands import (
    compute,
    config,
    inspect,
    projects,
    query,
    setup,
    supplement,
    traces,
    ui,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gigaflow",
        description="GigaFlow CLI — ingest Arize Phoenix traces and compute AIF analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  gigaflow setup
  gigaflow sync
  gigaflow traces
  gigaflow spans <trace_id>
  gigaflow query "SELECT trace_id, trace_name, groundedness FROM trace_metrics"
  gigaflow query --examples
  gigaflow compute "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"
  gigaflow compute "SELECT trace_id FROM trace_metrics WHERE env = 'prod'" --force
  gigaflow ui
  gigaflow inspect <trace_id>
  gigaflow inspect <trace_id> --cli
  gigaflow config show
  gigaflow config clear
        """,
    )
    parser.add_argument(
        "--backend",
        metavar="URL",
        default=None,
        help="GigaFlow API base URL (default: from config or http://localhost:8000/api/v1)",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="Path to gigaflow.env (default: auto-detects gigaflow.env in current directory)",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    setup.register(sub)
    traces.register(sub)
    inspect.register(sub)
    ui.register(sub)
    projects.register(sub)
    config.register(sub)
    query.register(sub)
    compute.register(sub)
    supplement.register(sub)

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    # Load gigaflow.env and inject keys into os.environ (without overriding existing vars)
    env_file = args.env_file or ("gigaflow.env" if os.path.exists("gigaflow.env") else None)
    if env_file:
        for key, value in load_env_file(env_file).items():
            os.environ.setdefault(key, value)

    cfg = _config.load()
    base_url = (args.backend or cfg.get("backend_url") or "http://localhost:8000/api/v1").rstrip("/")

    args.func(args, base_url)


if __name__ == "__main__":
    main()
