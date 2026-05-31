"""traces / spans commands."""

import sys

from gigaflow import _config, _fmt
from gigaflow._http import api, auth_error_hint, unreachable_hint
from gigaflow._setup import do_sync, run_wizard


def _fail(status, resp, base_url: str, what: str) -> None:
    if status is None:
        _fmt.fail(unreachable_hint(base_url))
    elif status in (401, 403):
        _fmt.fail(auth_error_hint())
    else:
        _fmt.fail(f"Failed to {what} ({status}): {resp}")
    sys.exit(1)


def register(sub) -> None:
    traces_p = sub.add_parser("traces", help="List all traces")
    traces_p.add_argument("--no-sync", action="store_true", help="Skip auto-sync")
    traces_p.set_defaults(func=_handle_traces)

    spans_p = sub.add_parser("spans", help="List spans for a trace")
    spans_p.add_argument("trace_id", help="Full trace UUID")
    spans_p.add_argument("--no-sync", action="store_true", help="Skip auto-sync")
    spans_p.set_defaults(func=_handle_spans)


# ── shared helper ──────────────────────────────────────────────────────────────

def _ensure_ready(base_url: str, auto_sync: bool = True, api_key: str | None = None) -> dict | None:
    config = _config.load()
    if not config.get("datasource_id"):
        print("No configuration found. Running setup wizard first...")
        print()
        config = run_wizard(base_url)
        if config is None:
            return None
        return config  # wizard already synced
    if auto_sync:
        _fmt.section("Syncing")
        do_sync(base_url, config["datasource_id"], api_key)
    return config


# ── handlers ───────────────────────────────────────────────────────────────────

def _handle_traces(args, base_url: str) -> None:
    config = _ensure_ready(base_url, not args.no_sync, getattr(args, "api_key", None))
    if config is None:
        sys.exit(1)

    _fmt.section("Traces")
    project_id = config.get("project_id")
    path = f"/traces/?project_id={project_id}&limit=1000" if project_id else "/traces/?limit=1000"
    status, resp = api(base_url, "GET", path, api_key=getattr(args, "api_key", None))
    if status != 200:
        _fail(status, resp, base_url, "list traces")

    traces = resp.get("traces", [])

    rows = []
    for t in traces:
        started = (t.get("started_at") or "")[:19].replace("T", " ")
        rows.append([t.get("trace_id", ""), t.get("trace_name", "-"), t.get("status", "-"), started])

    _fmt.table(rows, ["TRACE ID", "NAME", "STATUS", "STARTED AT"], max_col=40)
    print(f"  {len(traces)} trace(s)")
    if traces:
        print("  Get spans:  gigaflow spans <trace_id>")
        print("  Run AIF:    gigaflow run aif <trace_id>")
    print()


def _handle_spans(args, base_url: str) -> None:
    config = _ensure_ready(base_url, not args.no_sync, getattr(args, "api_key", None))
    if config is None:
        sys.exit(1)

    _fmt.section(f"Spans for trace {args.trace_id[:8]}…")
    status, resp = api(base_url, "GET", f"/traces/{args.trace_id}/spans", api_key=getattr(args, "api_key", None))
    if status != 200:
        _fail(status, resp, base_url, "get spans")

    spans = resp if isinstance(resp, list) else resp.get("spans", [])

    rows = []
    for s in spans:
        pd    = s.get("primitive_data") or {}
        ptype = s.get("primitive_type") or "-"
        extra = "-"
        if ptype == "llm_call":
            model  = pd.get("model", "")
            tokens = pd.get("prompt_tokens", "")
            extra  = f"{model} / {tokens} tok" if model else "-"
        elif ptype == "tool_invocation":
            extra = pd.get("tool_name", "-")
        elif ptype == "user_input":
            extra = (pd.get("content") or "-")[:30]

        started = (s.get("started_at") or "")[:19].replace("T", " ")
        rows.append([s.get("span_name", "-"), s.get("span_type", "-"), ptype, extra, started])

    _fmt.table(rows, ["SPAN NAME", "TYPE", "PRIMITIVE", "DETAIL", "STARTED AT"])
    classified = sum(1 for s in spans if s.get("primitive_type"))
    print(f"  {len(spans)} span(s) — {classified} classified, {len(spans) - classified} unclassified\n")
