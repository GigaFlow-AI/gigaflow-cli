"""run command — interactive trace analysis runner."""

import os
import sys
import webbrowser

from gigaflow import _config, _fmt
from gigaflow._http import api
from gigaflow._setup import do_sync, run_wizard


def register(sub) -> None:
    run_p = sub.add_parser("run", help="Interactive trace analysis")
    rsub  = run_p.add_subparsers(dest="run_action", metavar="<action>")

    aif_p = rsub.add_parser("aif", help="Run AIF analysis on a trace and open the viewer")
    aif_p.add_argument(
        "trace_id",
        nargs="?",
        default=None,
        metavar="TRACE_ID",
        help="Trace ID to analyse (skips interactive selection)",
    )
    aif_p.add_argument("--no-sync",    action="store_true", help="Skip auto-sync")
    aif_p.add_argument("--no-browser", action="store_true", help="Do not open browser")
    aif_p.set_defaults(func=_handle_aif)

    run_p.set_defaults(func=_handle_no_action)


def _handle_no_action(args, base_url: str) -> None:
    import argparse
    argparse.ArgumentParser(prog="gigaflow run").parse_args(["--help"])


def _handle_aif(args, base_url: str) -> None:
    # ── ensure configured ───────────────────────────────────────────────────
    config = _config.load()
    if not config.get("datasource_id"):
        print("No configuration found. Running setup wizard first...\n")
        config = run_wizard(base_url)
        if config is None:
            sys.exit(1)
    elif not args.no_sync and not args.trace_id:
        # Skip sync when a trace_id is given directly — user knows what they want
        _fmt.section("Syncing")
        do_sync(base_url, config["datasource_id"])

    # ── resolve trace_id ────────────────────────────────────────────────────
    if args.trace_id:
        trace_id = args.trace_id
    else:
        # Interactive selection
        project_id = config.get("project_id")
        path = f"/traces/?project_id={project_id}" if project_id else "/traces/"
        status, resp = api(base_url, "GET", path)
        if status != 200:
            _fmt.fail(f"Failed to list traces: {resp}")
            sys.exit(1)

        traces = resp.get("traces", [])
        if not traces:
            _fmt.fail("No traces found. Run 'gigaflow setup' to configure a datasource.")
            sys.exit(1)

        _fmt.section("Available Traces")
        for i, t in enumerate(traces, 1):
            started = (t.get("started_at") or "")[:16].replace("T", " ")
            name    = (t.get("trace_name") or "-")[:44]
            st      = t.get("status") or "-"
            print(f"  {i:3}.  {t['trace_id'][:8]}…  {name:<44}  [{st}]  {started}")
        print()

        try:
            choice = input("  Select trace (number or full ID): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)

        if not choice:
            sys.exit(0)

        if choice.isdigit():
            idx = int(choice) - 1
            if idx < 0 or idx >= len(traces):
                _fmt.fail(f"Invalid selection: {choice}")
                sys.exit(1)
            trace_id = traces[idx]["trace_id"]
        else:
            trace_id = choice

    # ── run AIF analysis ────────────────────────────────────────────────────
    _fmt.section("Running AIF analysis")
    _fmt.info("Decomposing trace into atoms (this may take a minute)…")
    aif_body: dict = {}
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        aif_body["api_key"] = api_key
    else:
        _fmt.fail("No OpenAI API key found. Set OPENAI_API_KEY in gigaflow.env or as an environment variable.")
        sys.exit(1)
    status, resp = api(base_url, "POST", f"/aif/{trace_id}", aif_body)
    if status != 200:
        _fmt.fail(f"AIF analysis failed ({status}): {resp.get('detail', resp)}")
        sys.exit(1)
    _fmt.ok("AIF analysis complete")

    # ── open browser ────────────────────────────────────────────────────────
    viewer_url = f"{base_url.replace('/api/v1', '')}/aif/{trace_id}"
    _fmt.info(f"Opening viewer: {viewer_url}")
    if not args.no_browser:
        webbrowser.open(viewer_url)
    print()
