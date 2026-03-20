"""
gigaflow — CLI entry point.

Commands:
  gigaflow run aif [TRACE_ID]        Run AIF analysis (interactive if TRACE_ID omitted)
  gigaflow setup                     Configure GigaFlow with an Arize Phoenix datasource
  gigaflow sync                      Re-sync traces from the configured datasource
  gigaflow traces                    List all traces (auto-syncs first)
  gigaflow spans <trace_id>          List spans for a trace (auto-syncs first)
  gigaflow inspect <trace_id>        Visualize a trace (orchestration graph + full span data)
  gigaflow projects                  List all projects
  gigaflow config show               Show saved configuration
  gigaflow config clear              Clear saved configuration
"""

import argparse
import os
import sys

from gigaflow import _config
from gigaflow._setup import load_env_file
from gigaflow.commands import config, inspect, projects, run, setup, traces


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gigaflow",
        description="GigaFlow CLI — connect Arize Phoenix traces to GigaFlow and analyze them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  gigaflow run aif
  gigaflow run aif <trace_id>
  gigaflow setup
  gigaflow traces
  gigaflow spans <trace_id>
  gigaflow inspect <trace_id>
  gigaflow inspect <trace_id> --cli
  gigaflow inspect <trace_id> --port 8080
  gigaflow sync
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
    run.register(sub)
    setup.register(sub)
    traces.register(sub)
    inspect.register(sub)
    projects.register(sub)
    config.register(sub)

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
