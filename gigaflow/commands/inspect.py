"""trace inspect command — visualize a GigaFlow trace.

Opens the backend's interactive AIF viewer in the browser.  The viewer shows
span timelines always; AIF tabs (Orchestration, Atomic View, Metrics) are shown
when AIF has been computed and are replaced with a "Compute AIF" prompt otherwise.

Use --cli for a terminal-only span tree without opening the browser.
"""

import sys
import webbrowser

from gigaflow import _fmt
from gigaflow._http import api


def register(sub) -> None:
    p = sub.add_parser(
        "inspect",
        help="Open the interactive AIF viewer for a trace",
    )
    p.add_argument("trace_id", help="Full trace UUID")
    p.add_argument("--cli", action="store_true", help="Print CLI tree view instead of opening browser")
    p.add_argument("--no-browser", action="store_true", help="Print viewer URL but do not open browser")
    p.set_defaults(func=_handle_inspect)


# ── Handler ─────────────────────────────────────────────────────────────────

def _handle_inspect(args, base_url: str) -> None:
    _fmt.header("GigaFlow Trace Inspector")

    _fmt.section(f"Fetching trace {args.trace_id[:8]}…")
    status, trace = api(base_url, "GET", f"/traces/{args.trace_id}")
    if status != 200:
        _fmt.fail(f"Failed to fetch trace ({status}): {trace}")
        sys.exit(1)

    status, spans_resp = api(base_url, "GET", f"/traces/{args.trace_id}/spans")
    if status != 200:
        _fmt.fail(f"Failed to fetch spans ({status}): {spans_resp}")
        sys.exit(1)

    spans = spans_resp if isinstance(spans_resp, list) else spans_resp.get("spans", [])
    _fmt.ok(f"Loaded {len(spans)} span(s)")

    if args.cli:
        _render_cli(trace, spans)
    else:
        viewer_url = f"{base_url.replace('/api/v1', '')}/aif/{args.trace_id}"
        _fmt.info(f"Viewer: {viewer_url}")
        if not args.no_browser:
            _open_browser(viewer_url)
        print()


# ── CLI renderer ─────────────────────────────────────────────────────────────

_PTYPE_ICON = {
    "user_input":      "[USR]",
    "tool_invocation": "[TUL]",
    "llm_call":        "[LLM]",
}


def _render_cli(trace: dict, spans: list) -> None:
    _fmt.section("Trace Metadata")
    for key, val in [
        ("id",        trace.get("trace_id")),
        ("name",      trace.get("trace_name", "-")),
        ("service",   trace.get("service_name", "-")),
        ("env",       trace.get("environment", "-")),
        ("status",    trace.get("status", "-")),
        ("started",   (trace.get("started_at") or "")[:19].replace("T", " ")),
        ("completed", (trace.get("completed_at") or "")[:19].replace("T", " ")),
        ("duration",  f"{trace.get('duration_ms', '-')} ms"),
    ]:
        print(f"  {key:<12}{val}")

    # Build span index and tree
    span_map = {s["span_id"]: s for s in spans}
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for s in spans:
        pid = s.get("parent_span_id")
        if pid and pid in span_map:
            children.setdefault(pid, []).append(s["span_id"])
        else:
            roots.append(s["span_id"])

    _fmt.section("Span Tree")
    print("  [USR]=user_input  [TUL]=tool_invocation  [LLM]=llm_call  [   ]=unclassified\n")

    def _print_span(sid: str, indent: int = 0, last: bool = True) -> None:
        s = span_map[sid]
        ptype = s.get("primitive_type") or ""
        icon  = _PTYPE_ICON.get(ptype, "[   ]")
        name  = s.get("span_name") or s.get("operation_name") or sid[:8]
        dur   = f"{s.get('duration_ms', '?')} ms"
        status = s.get("status", "")

        branch = "└─" if last else "├─"
        pre    = "  " + "  " * indent
        print(f"{pre}{branch}{icon} {name}  ({status}, {dur})")

        pd = s.get("primitive_data") or {}
        if pd:
            pre2 = "  " + "  " * indent + ("   " if last else "│  ")
            for k, v in pd.items():
                val = str(v) if v is not None else "-"
                val = val[:70] + "…" if len(val) > 70 else val
                print(f"{pre2}  {k}: {val}")

        kids = children.get(sid, [])
        for i, cid in enumerate(kids):
            _print_span(cid, indent + 1, i == len(kids) - 1)

    for i, rid in enumerate(roots):
        _print_span(rid, last=i == len(roots) - 1)

    # Orchestration summary
    user_inputs  = [s for s in spans if s.get("primitive_type") == "user_input"]
    tool_calls   = [s for s in spans if s.get("primitive_type") == "tool_invocation"]
    llm_calls    = [s for s in spans if s.get("primitive_type") == "llm_call"]
    unclassified = [s for s in spans if not s.get("primitive_type")]

    _fmt.section("Orchestration Flow")
    if user_inputs:
        for u in user_inputs:
            pd = u.get("primitive_data") or {}
            content = pd.get("content") or pd.get("message") or pd.get("text") or "-"
            print(f"  USER QUERY  →  {str(content)[:80]}")
    else:
        print("  USER QUERY  →  (no user_input spans found)")

    print()
    if tool_calls:
        print(f"  TOOL CALLS ({len(tool_calls)}):")
        for t in tool_calls:
            pd   = t.get("primitive_data") or {}
            name = pd.get("tool_name") or t.get("span_name") or "-"
            out  = pd.get("tool_output") or pd.get("output") or pd.get("result") or ""
            out  = str(out)[:60] + "…" if len(str(out)) > 60 else str(out)
            print(f"    ↳ {name}  output: {out}")
    else:
        print("  TOOL CALLS  →  (none found)")

    print()
    if llm_calls:
        print(f"  LLM ANSWERS ({len(llm_calls)}):")
        for lc in llm_calls:
            pd     = lc.get("primitive_data") or {}
            model  = pd.get("model") or "-"
            ptok   = pd.get("prompt_tokens") or "-"
            ctok   = pd.get("completion_tokens") or "-"
            answer = pd.get("completion") or pd.get("response") or pd.get("output") or ""
            answer = str(answer)[:60] + "…" if len(str(answer)) > 60 else str(answer)
            print(f"    ↳ {model}  (prompt={ptok} tok, completion={ctok} tok)")
            if answer:
                print(f"       {answer}")
    else:
        print("  LLM ANSWERS →  (none found)")

    print()
    _fmt.section("Classification Summary")
    total = len(spans)
    print(f"  user_input:      {len(user_inputs):>3}  ({100*len(user_inputs)//total if total else 0}%)")
    print(f"  tool_invocation: {len(tool_calls):>3}  ({100*len(tool_calls)//total if total else 0}%)")
    print(f"  llm_call:        {len(llm_calls):>3}  ({100*len(llm_calls)//total if total else 0}%)")
    print(f"  unclassified:    {len(unclassified):>3}  ({100*len(unclassified)//total if total else 0}%)")
    print()


# ── Browser helper ────────────────────────────────────────────────────────────

def _open_browser(url: str) -> None:
    """Open url in the user's browser, handling WSL2 where webbrowser fails."""
    import time
    time.sleep(0.3)

    # Detect WSL2 — /proc/version contains "microsoft" or "WSL"
    try:
        with open("/proc/version") as f:
            is_wsl = "microsoft" in f.read().lower()
    except OSError:
        is_wsl = False

    if is_wsl:
        import subprocess
        # Use Windows cmd.exe to open the default browser
        result = subprocess.run(
            ["cmd.exe", "/c", "start", "", url],
            capture_output=True,
        )
        if result.returncode == 0:
            return
        # Fallback: powershell
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"],
            capture_output=True,
        )
    else:
        webbrowser.open(url)
