"""trace inspect command — visualize a GigaFlow trace (orchestration graph + full data)."""

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from gigaflow import _fmt
from gigaflow._http import api


def register(sub) -> None:
    p = sub.add_parser(
        "inspect",
        help="Visualize a GigaFlow trace (orchestration graph + full span data)",
    )
    p.add_argument("trace_id", help="Full trace UUID")
    p.add_argument("--cli", action="store_true", help="Print CLI tree view instead of web UI")
    p.add_argument("--port", type=int, default=0, help="Port for the web server (default: random)")
    p.add_argument("--no-browser", action="store_true", help="Start web server but do not open browser")
    p.set_defaults(func=_handle_inspect)


# ── handler ────────────────────────────────────────────────────────────────────

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
        _render_web(trace, spans, args)


# ── CLI renderer ───────────────────────────────────────────────────────────────

_PTYPE_ICON = {
    "user_input":      "[USR]",
    "tool_invocation": "[TUL]",
    "llm_call":        "[LLM]",
}
_PTYPE_LABEL = {
    "user_input":      "user_input",
    "tool_invocation": "tool_invocation",
    "llm_call":        "llm_call",
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
    print(f"  [USR]=user_input  [TUL]=tool_invocation  [LLM]=llm_call  [   ]=unclassified\n")

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


# ── browser helper ─────────────────────────────────────────────────────────────

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


# ── Web renderer ───────────────────────────────────────────────────────────────

def _render_web(trace: dict, spans: list, args) -> None:
    html = _build_html(trace, spans)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", args.port), _Handler)
    port   = server.server_address[1]
    url    = f"http://127.0.0.1:{port}"

    _fmt.ok(f"Serving trace inspector at {url}")
    _fmt.info("Press Ctrl+C to stop.")
    print()

    if not args.no_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# ── HTML builder (server-rendered) ────────────────────────────────────────────

_PTYPE_COLOR = {
    "user_input":      "#3b82f6",
    "tool_invocation": "#10b981",
    "llm_call":        "#f59e0b",
}
_PTYPE_LABEL = {
    "user_input":      "USR",
    "tool_invocation": "TUL",
    "llm_call":        "LLM",
}


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _badge(ptype: str | None) -> str:
    color = _PTYPE_COLOR.get(ptype or "", "#475569")
    label = _PTYPE_LABEL.get(ptype or "", "---")
    return (f'<span style="background:{color}22;color:{color};border:1px solid {color}66;'
            f'border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700;'
            f'font-family:monospace">{label}</span>')


def _build_html(trace: dict, spans: list) -> str:
    parts = []
    p = parts.append

    ptype_of = {
        "user_input": "user", "tool_invocation": "tool", "llm_call": "llm"
    }

    # ── CSS ──
    p("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GigaFlow Trace Inspector</title>
<style>
body { margin:0; font-family:system-ui,sans-serif; background:#0f1117; color:#e2e8f0; }
a { color:#7dd3fc; }
.header { background:#1e2233; border-bottom:2px solid #3b82f6; padding:14px 20px; }
.header h1 { margin:0 0 4px; font-size:18px; color:#7dd3fc; }
.meta { font-size:12px; color:#94a3b8; display:flex; gap:18px; flex-wrap:wrap; margin-top:6px; }
.meta span { color:#e2e8f0; }
.section { padding:14px 20px; border-bottom:1px solid #1e2233; }
.section-title { font-size:11px; font-weight:700; letter-spacing:.8px; text-transform:uppercase;
  color:#475569; margin-bottom:10px; }
.flow-row { display:flex; align-items:flex-start; gap:0; flex-wrap:wrap; }
.flow-col { background:#161b2e; border-radius:8px; padding:10px 14px; min-width:160px; flex:1; }
.flow-col h3 { font-size:11px; font-weight:700; margin:0 0 8px; }
.flow-col ul { margin:0; padding:0 0 0 14px; font-size:12px; color:#cbd5e1; }
.flow-arrow { font-size:24px; color:#334155; padding:24px 8px 0; }
.span-list { padding:14px 20px; }
details { background:#161b2e; border:1px solid #1e2a3a; border-radius:8px;
  margin-bottom:8px; overflow:hidden; }
details[open] { border-color:#2d3f55; }
summary { cursor:pointer; padding:10px 14px; display:flex; align-items:center;
  gap:8px; font-size:13px; list-style:none; user-select:none; }
summary::-webkit-details-marker { display:none; }
summary:hover { background:#1e2a3a; }
.summary-name { flex:1; color:#e2e8f0; font-family:monospace; font-size:12px; }
.summary-meta { font-size:11px; color:#64748b; font-family:monospace; }
.detail-body { padding:12px 14px 14px; border-top:1px solid #1e2a3a; }
table { border-collapse:collapse; width:100%; font-size:12px; }
td { padding:3px 8px 3px 0; vertical-align:top; }
td:first-child { color:#64748b; white-space:nowrap; width:160px; }
td:last-child { color:#e2e8f0; font-family:monospace; word-break:break-all; }
.null { color:#334155; font-style:italic; }
pre.json { background:#0d1117; border:1px solid #1e2233; border-radius:6px;
  padding:10px; font-size:11px; color:#94a3b8; white-space:pre-wrap;
  word-break:break-all; max-height:200px; overflow-y:auto; margin:6px 0 0; }
.stats { display:flex; gap:16px; font-size:12px; }
.dot { width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:4px; }
</style>
</head>
<body>
""")

    # ── header ──
    status = trace.get("status") or "unknown"
    status_colors = {"completed": "#34d399", "running": "#60a5fa", "failed": "#fb7185"}
    sc = status_colors.get(status, "#94a3b8")
    p(f'<div class="header">')
    p(f'<h1>GigaFlow Trace Inspector</h1>')
    p(f'<div class="meta">')
    p(f'<div>ID: <span>{_esc(trace.get("trace_id",""))}</span></div>')
    p(f'<div>Name: <span>{_esc(trace.get("trace_name",""))}</span></div>')
    p(f'<div>Status: <span style="color:{sc}">{_esc(status)}</span></div>')
    p(f'<div>Service: <span>{_esc(trace.get("service_name") or "-")}</span></div>')
    p(f'<div>Started: <span>{_esc((trace.get("started_at") or "")[:19].replace("T"," "))}</span></div>')
    p(f'<div>Duration: <span>{_esc(trace.get("duration_ms") or "-")} ms</span></div>')
    p(f'<div>Spans: <span>{len(spans)}</span></div>')
    p('</div></div>')

    # ── stats ──
    by_type: dict[str, list] = {"user_input": [], "tool_invocation": [], "llm_call": [], "none": []}
    for s in spans:
        pt = s.get("primitive_type") or ""
        by_type.get(pt, by_type["none"]).append(s)

    p('<div class="section">')
    p('<div class="section-title">Classification</div>')
    p('<div class="stats">')
    for pt, color, label in [
        ("user_input",      "#3b82f6", "user_input"),
        ("tool_invocation", "#10b981", "tool_invocation"),
        ("llm_call",        "#f59e0b", "llm_call"),
        ("none",            "#475569", "unclassified"),
    ]:
        count = len(by_type[pt])
        p(f'<div><span class="dot" style="background:{color}"></span>{count} {label}</div>')
    p('</div></div>')

    # ── orchestration flow ──
    p('<div class="section">')
    p('<div class="section-title">Orchestration Flow</div>')
    p('<div class="flow-row">')
    for pt, color, label, key_fn in [
        ("user_input",      "#3b82f6", "USER INPUT",   lambda pd: pd.get("content") or pd.get("message") or pd.get("text")),
        ("tool_invocation", "#10b981", "TOOL CALLS",   lambda pd: pd.get("tool_name")),
        ("llm_call",        "#f59e0b", "LLM ANSWERS",  lambda pd: pd.get("model")),
    ]:
        p(f'<div class="flow-col" style="border-left:3px solid {color}">')
        p(f'<h3 style="color:{color}">{label}</h3>')
        items = by_type[pt]
        if items:
            p('<ul>')
            for s in items:
                pd = s.get("primitive_data") or {}
                name = key_fn(pd) or s.get("span_name") or s.get("span_id","")[:12]
                p(f'<li>{_esc(str(name)[:60])}</li>')
            p('</ul>')
        else:
            p('<div style="color:#334155;font-size:12px;font-style:italic">none</div>')
        p('</div>')
        if pt != "llm_call":
            p('<div class="flow-arrow">→</div>')
    p('</div></div>')

    # ── span list ──
    p('<div class="span-list">')
    p('<div class="section-title" style="padding:0 0 10px">Spans</div>')

    for s in spans:
        pt    = s.get("primitive_type") or ""
        color = _PTYPE_COLOR.get(pt, "#475569")
        name  = s.get("span_name") or s.get("operation_name") or s.get("span_id","")[:16]
        pd    = s.get("primitive_data") or {}
        meta  = s.get("metadata_json") or {}

        # summary line hint
        if pt == "llm_call":
            hint = pd.get("model","")
        elif pt == "tool_invocation":
            hint = pd.get("tool_name","")
        elif pt == "user_input":
            hint = str(pd.get("content") or pd.get("message") or "")[:40]
        else:
            hint = s.get("span_type","")

        p('<details>')
        p(f'<summary>')
        p(f'<span style="width:10px;height:10px;border-radius:50%;background:{color};flex-shrink:0;display:inline-block"></span>')
        p(_badge(pt))
        p(f'<span class="summary-name">{_esc(name)}</span>')
        p(f'<span class="summary-meta">{_esc(hint)}</span>')
        p('</summary>')
        p('<div class="detail-body">')

        # metadata table
        p('<table>')
        for k, v in [
            ("span_id",        s.get("span_id")),
            ("trace_id",       s.get("trace_id")),
            ("parent_span_id", s.get("parent_span_id")),
            ("span_name",      s.get("span_name")),
            ("span_type",      s.get("span_type")),
            ("operation_name", s.get("operation_name")),
            ("service_name",   s.get("service_name")),
            ("status",         s.get("status")),
            ("started_at",     s.get("started_at")),
            ("completed_at",   s.get("completed_at")),
            ("duration_ms",    s.get("duration_ms")),
            ("primitive_type", s.get("primitive_type")),
        ]:
            if v is None:
                p(f'<tr><td>{_esc(k)}</td><td class="null">null</td></tr>')
            else:
                p(f'<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>')

        # primitive_data fields
        if pd:
            for k, v in pd.items():
                p(f'<tr><td style="color:#10b981">{_esc(k)}</td><td>{_esc(v)}</td></tr>')

        p('</table>')

        # raw metadata_json
        if meta:
            p('<div style="margin-top:8px;font-size:11px;color:#475569">metadata_json</div>')
            p(f'<pre class="json">{_esc(json.dumps(meta, indent=2))}</pre>')

        p('</div></details>')

    p('</div>')
    p('</body></html>')
    return "\n".join(parts)

# (old JS template removed — HTML is now generated server-side in _build_html)

_UNUSED = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GigaFlow Trace Inspector</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0f1117;
    color: #e2e8f0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ── header ── */
  .header {
    background: #1e2233;
    border-bottom: 1px solid #2d3348;
    padding: 12px 20px;
    flex-shrink: 0;
  }
  .header-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .header-title { font-size: 18px; font-weight: 700; color: #7dd3fc; }
  .header-sub { font-size: 12px; color: #94a3b8; font-family: monospace; }
  .badge {
    display: inline-flex; align-items: center;
    padding: 2px 8px; border-radius: 12px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.4px;
  }
  .badge-completed { background: #064e3b; color: #34d399; }
  .badge-running   { background: #1e3a5f; color: #60a5fa; }
  .badge-failed    { background: #4c0519; color: #fb7185; }
  .badge-default   { background: #1e2a3a; color: #94a3b8; }
  .meta-row { display: flex; gap: 20px; margin-top: 8px; flex-wrap: wrap; }
  .meta-item { font-size: 12px; color: #64748b; }
  .meta-item span { color: #cbd5e1; }

  /* ── stats bar ── */
  .stats-bar {
    background: #161b2e;
    border-bottom: 1px solid #2d3348;
    padding: 8px 20px;
    display: flex; gap: 16px; align-items: center;
    flex-shrink: 0;
    flex-wrap: wrap;
  }
  .stat { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .stat-dot { width: 10px; height: 10px; border-radius: 50%; }
  .dot-user   { background: #3b82f6; }
  .dot-tool   { background: #10b981; }
  .dot-llm    { background: #f59e0b; }
  .dot-none   { background: #475569; }
  .stat-count { font-weight: 700; color: #e2e8f0; }
  .stat-label { color: #64748b; }

  /* ── flow bar ── */
  .flow-bar {
    background: #0f1421;
    border-bottom: 1px solid #2d3348;
    padding: 10px 20px;
    display: flex; align-items: center; gap: 0;
    flex-shrink: 0;
    overflow-x: auto;
    min-height: 52px;
  }
  .flow-node {
    background: #1e2a3a;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
    border: 1px solid #2d3f55;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    white-space: nowrap;
    max-width: 200px;
    overflow: hidden; text-overflow: ellipsis;
  }
  .flow-node:hover { border-color: #4a90e2; background: #1e3a5f; }
  .flow-node.fn-user { border-color: #2563eb; }
  .flow-node.fn-tool { border-color: #059669; }
  .flow-node.fn-llm  { border-color: #d97706; }
  .flow-node-label { font-size: 10px; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 2px; }
  .fn-user .flow-node-label { color: #60a5fa; }
  .fn-tool .flow-node-label { color: #34d399; }
  .fn-llm  .flow-node-label { color: #fbbf24; }
  .flow-node-name { color: #e2e8f0; font-size: 12px; }
  .flow-arrow { color: #334155; font-size: 18px; padding: 0 6px; flex-shrink: 0; }
  .flow-group { display: flex; gap: 6px; align-items: center; flex-wrap: nowrap; }
  .flow-empty { color: #334155; font-size: 12px; font-style: italic; }

  /* ── main layout ── */
  .main {
    flex: 1;
    display: flex;
    overflow: hidden;
    min-height: 0;
  }

  /* ── left panel: tabs ── */
  .left-panel {
    width: 340px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    border-right: 1px solid #2d3348;
    min-height: 0;
  }
  .tab-bar {
    display: flex;
    border-bottom: 1px solid #2d3348;
    background: #161b2e;
    flex-shrink: 0;
  }
  .tab {
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
    color: #64748b;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab.active { color: #7dd3fc; border-bottom-color: #3b82f6; }
  .tab:hover:not(.active) { color: #94a3b8; }

  .tab-content { flex: 1; overflow-y: auto; min-height: 0; }

  /* ── span tree ── */
  .tree-panel { padding: 8px 0; }
  .tree-search {
    margin: 8px 10px;
    padding: 6px 10px;
    background: #1e2233;
    border: 1px solid #2d3348;
    border-radius: 6px;
    color: #e2e8f0;
    font-size: 12px;
    width: calc(100% - 20px);
    outline: none;
  }
  .tree-search:focus { border-color: #3b82f6; }
  .tree-node {
    padding: 5px 10px 5px 0;
    cursor: pointer;
    user-select: none;
    border-left: 3px solid transparent;
    transition: background 0.1s;
  }
  .tree-node:hover { background: #1e2233; }
  .tree-node.selected { background: #1e3a5f; border-left-color: #3b82f6; }
  .tree-row { display: flex; align-items: center; gap: 6px; }
  .tree-toggle { width: 16px; font-size: 10px; color: #475569; flex-shrink: 0; text-align: center; }
  .tree-icon { font-size: 13px; flex-shrink: 0; }
  .tree-name { font-size: 12px; color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
  .tree-badge {
    font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
    flex-shrink: 0; letter-spacing: 0.3px;
  }
  .tb-user { background: #1d4ed8; color: #bfdbfe; }
  .tb-tool { background: #065f46; color: #a7f3d0; }
  .tb-llm  { background: #92400e; color: #fde68a; }
  .tb-none { background: #1e293b; color: #475569; }
  .tree-children { display: block; }
  .tree-children.collapsed { display: none; }

  /* ── graph panel ── */
  .graph-panel { padding: 8px; overflow: auto; height: 100%; }
  .graph-wrap { position: relative; }
  svg.graph { display: block; }
  .gnode { cursor: pointer; }
  .gnode rect {
    rx: 8; ry: 8;
    transition: opacity 0.15s;
  }
  .gnode:hover rect { opacity: 0.85; }
  .gnode.selected rect { stroke-width: 2.5 !important; }
  .gnode text { pointer-events: none; }

  /* ── right panel: details ── */
  .detail-panel {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
  }
  .detail-header {
    padding: 10px 16px;
    border-bottom: 1px solid #2d3348;
    background: #161b2e;
    flex-shrink: 0;
  }
  .detail-title { font-size: 13px; font-weight: 700; color: #7dd3fc; }
  .detail-sub { font-size: 11px; color: #475569; margin-top: 2px; font-family: monospace; }
  .detail-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    min-height: 0;
  }
  .detail-empty {
    display: flex; align-items: center; justify-content: center;
    height: 100%; color: #334155; font-size: 14px;
  }
  .detail-section { margin-bottom: 16px; }
  .detail-section-title {
    font-size: 10px; font-weight: 700; letter-spacing: 0.8px;
    color: #475569; text-transform: uppercase;
    margin-bottom: 6px; padding-bottom: 4px;
    border-bottom: 1px solid #1e2233;
  }
  .detail-row { display: flex; gap: 8px; margin-bottom: 4px; font-size: 12px; }
  .detail-key { color: #64748b; flex-shrink: 0; width: 130px; }
  .detail-val { color: #e2e8f0; word-break: break-all; font-family: monospace; }
  .detail-val.null { color: #334155; font-style: italic; }
  .json-block {
    background: #0d1117;
    border: 1px solid #1e2233;
    border-radius: 6px;
    padding: 10px;
    font-size: 11px;
    font-family: monospace;
    color: #94a3b8;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 300px;
    overflow-y: auto;
    line-height: 1.5;
  }
  .json-key   { color: #7dd3fc; }
  .json-str   { color: #86efac; }
  .json-num   { color: #fbbf24; }
  .json-bool  { color: #f472b6; }
  .json-null  { color: #475569; }

  /* ── scrollbars ── */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: #0f1117; }
  ::-webkit-scrollbar-thumb { background: #2d3348; border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: #3d4458; }
</style>
</head>
<body>

<div id="app"></div>

<script type="application/json" id="gf-trace">__TRACE_JSON__</script>
<script type="application/json" id="gf-spans">__SPANS_JSON__</script>

<script>
window.onerror = function(msg, src, line, col, err) {
  document.body.style.background = '#1a0000';
  document.body.innerHTML = '<div style="color:#ff6b6b;font-family:monospace;padding:24px;white-space:pre-wrap">'
    + '<b>JS Error — please report this:</b>\n\n'
    + msg + '\n' + (src||'') + ':' + line + ':' + col
    + (err ? '\n\n' + err.stack : '') + '</div>';
  return false;
};

(function () {
  try {

  const TRACE = JSON.parse(document.getElementById('gf-trace').textContent);
  const SPANS = JSON.parse(document.getElementById('gf-spans').textContent);

  // ── helpers ────────────────────────────────────────────────────────────────

  const PTYPE = { user_input: 'user', tool_invocation: 'tool', llm_call: 'llm' };
  const COLORS = { user: '#3b82f6', tool: '#10b981', llm: '#f59e0b', none: '#475569' };
  const ICONS  = { user: '👤', tool: '🔧', llm: '🤖', none: '⬜' };
  const BADGE_CLASS = { user: 'tb-user', tool: 'tb-tool', llm: 'tb-llm', none: 'tb-none' };
  const BADGE_LABEL = { user: 'USR', tool: 'TUL', llm: 'LLM', none: '---' };
  const NODE_CLASS  = { user: 'fn-user', tool: 'fn-tool', llm: 'fn-llm', none: '' };

  function ptypeOf(s) { return PTYPE[s.primitive_type] || 'none'; }
  function colorOf(s) { return COLORS[ptypeOf(s)]; }

  function shortId(id) { return id ? id.slice(0, 8) + '…' : '-'; }
  function fmtTime(ts) { return ts ? ts.slice(0, 19).replace('T', ' ') : '-'; }
  function fmtDur(ms) { return ms != null ? ms + ' ms' : '-'; }

  // Build parent→children map and find roots
  const spanMap = {};
  SPANS.forEach(s => { spanMap[s.span_id] = s; });

  const childMap = {};
  const roots = [];
  SPANS.forEach(s => {
    const pid = s.parent_span_id;
    if (pid && spanMap[pid]) {
      if (!childMap[pid]) childMap[pid] = [];
      childMap[pid].push(s.span_id);
    } else {
      roots.push(s.span_id);
    }
  });

  // Classify
  const byType = { user: [], tool: [], llm: [], none: [] };
  SPANS.forEach(s => byType[ptypeOf(s)].push(s));

  // Selected span state
  let selectedId = null;

  // Active tab: 'tree' | 'graph'
  let activeTab = 'tree';

  // ── render ──────────────────────────────────────────────────────────────────

  function render() {
    const app = document.getElementById('app');
    app.innerHTML = '';
    app.style.display = 'flex';
    app.style.flexDirection = 'column';
    app.style.height = '100vh';

    app.appendChild(buildHeader());
    app.appendChild(buildStatsBar());
    app.appendChild(buildFlowBar());

    const main = el('div', { class: 'main' });
    main.appendChild(buildLeftPanel());
    main.appendChild(buildDetailPanel());
    app.appendChild(main);

    if (activeTab === 'graph') renderGraph();
  }

  // ── header ──────────────────────────────────────────────────────────────────

  function buildHeader() {
    const status = TRACE.status || 'unknown';
    const bclass = { completed: 'badge-completed', running: 'badge-running', failed: 'badge-failed' }[status] || 'badge-default';
    const h = el('div', { class: 'header' });
    const top = el('div', { class: 'header-top' });
    top.appendChild(el('span', { class: 'header-title', text: 'GigaFlow Trace Inspector' }));
    top.appendChild(el('span', { class: 'header-sub', text: TRACE.trace_id || '-' }));
    top.appendChild(el('span', { class: `badge ${bclass}`, text: status }));
    h.appendChild(top);
    const meta = el('div', { class: 'meta-row' });
    [
      ['Name', TRACE.trace_name || '-'],
      ['Service', TRACE.service_name || '-'],
      ['Env', TRACE.environment || '-'],
      ['Started', fmtTime(TRACE.started_at)],
      ['Duration', fmtDur(TRACE.duration_ms)],
      ['Spans', SPANS.length],
    ].forEach(([k, v]) => {
      const m = el('div', { class: 'meta-item' });
      m.innerHTML = `${k}: <span>${v}</span>`;
      meta.appendChild(m);
    });
    h.appendChild(meta);
    return h;
  }

  // ── stats bar ───────────────────────────────────────────────────────────────

  function buildStatsBar() {
    const bar = el('div', { class: 'stats-bar' });
    [
      ['dot-user', byType.user.length, 'user_input'],
      ['dot-tool', byType.tool.length, 'tool_invocation'],
      ['dot-llm',  byType.llm.length,  'llm_call'],
      ['dot-none', byType.none.length, 'unclassified'],
    ].forEach(([dot, count, label]) => {
      const s = el('div', { class: 'stat' });
      s.innerHTML = `<div class="stat-dot ${dot}"></div><span class="stat-count">${count}</span><span class="stat-label">${label}</span>`;
      bar.appendChild(s);
    });
    return bar;
  }

  // ── flow bar ─────────────────────────────────────────────────────────────────

  function buildFlowBar() {
    const bar = el('div', { class: 'flow-bar' });
    const groups = [
      ['user', 'USER INPUT'],
      ['tool', 'TOOL CALLS'],
      ['llm',  'LLM ANSWERS'],
    ];
    groups.forEach(([type, label], gi) => {
      const spans = byType[type];
      if (gi > 0) {
        bar.appendChild(el('div', { class: 'flow-arrow', text: '→' }));
      }
      if (spans.length === 0) {
        const n = el('div', { class: `flow-node ${NODE_CLASS[type]}` });
        n.innerHTML = `<div class="flow-node-label">${label}</div><div class="flow-empty">none</div>`;
        bar.appendChild(n);
      } else {
        const grp = el('div', { class: 'flow-group' });
        spans.forEach((s, i) => {
          if (i > 0) grp.appendChild(el('span', { class: 'flow-arrow', text: '|' }));
          const pd = s.primitive_data || {};
          const name =
            (type === 'user' ? (pd.content || pd.message || pd.text) : null) ||
            (type === 'tool' ? pd.tool_name : null) ||
            (type === 'llm'  ? pd.model    : null) ||
            s.span_name || shortId(s.span_id);
          const n = el('div', { class: `flow-node ${NODE_CLASS[type]}` });
          n.title = name;
          n.innerHTML = `<div class="flow-node-label">${label}</div><div class="flow-node-name">${esc(String(name).slice(0, 40))}</div>`;
          n.addEventListener('click', () => selectSpan(s.span_id));
          grp.appendChild(n);
        });
        bar.appendChild(grp);
      }
    });
    return bar;
  }

  // ── left panel ──────────────────────────────────────────────────────────────

  function buildLeftPanel() {
    const panel = el('div', { class: 'left-panel' });

    const tabs = el('div', { class: 'tab-bar' });
    ['tree', 'graph'].forEach(t => {
      const tab = el('div', { class: `tab ${activeTab === t ? 'active' : ''}`, text: t === 'tree' ? 'Span Tree' : 'Graph' });
      tab.addEventListener('click', () => { activeTab = t; render(); });
      tabs.appendChild(tab);
    });
    panel.appendChild(tabs);

    const content = el('div', { class: 'tab-content' });
    if (activeTab === 'tree') {
      content.appendChild(buildTree());
    } else {
      content.appendChild(buildGraphContainer());
    }
    panel.appendChild(content);
    return panel;
  }

  // ── span tree ───────────────────────────────────────────────────────────────

  const collapsed = new Set();

  function buildTree() {
    const wrap = el('div', { class: 'tree-panel' });
    const search = el('input', { class: 'tree-search', placeholder: 'Filter spans…' });
    search.setAttribute('type', 'text');
    let filter = '';
    search.addEventListener('input', e => {
      filter = e.target.value.toLowerCase();
      renderNodes(filter);
    });
    wrap.appendChild(search);
    const list = el('div');
    wrap.appendChild(list);
    function renderNodes(f) {
      list.innerHTML = '';
      roots.forEach(rid => list.appendChild(buildTreeNode(rid, 0, f)));
    }
    renderNodes('');
    return wrap;
  }

  function buildTreeNode(sid, depth, filter) {
    const s = spanMap[sid];
    if (!s) return el('span');
    const name = (s.span_name || s.operation_name || sid).toLowerCase();
    const pt = ptypeOf(s);
    const kids = childMap[sid] || [];

    // visibility: show if self or any descendant matches
    function hasMatch(id) {
      const sp = spanMap[id];
      if (!sp) return false;
      if (!filter) return true;
      const n = (sp.span_name || sp.operation_name || id).toLowerCase();
      if (n.includes(filter) || (sp.primitive_type || '').includes(filter)) return true;
      return (childMap[id] || []).some(cid => hasMatch(cid));
    }
    if (filter && !hasMatch(sid)) return el('span');

    const wrap = el('div');
    const node = el('div', { class: `tree-node ${selectedId === sid ? 'selected' : ''}` });
    node.style.paddingLeft = (10 + depth * 18) + 'px';

    const row = el('div', { class: 'tree-row' });
    const toggle = el('span', { class: 'tree-toggle' });
    if (kids.length > 0) {
      toggle.textContent = collapsed.has(sid) ? '▶' : '▼';
      toggle.addEventListener('click', e => {
        e.stopPropagation();
        collapsed.has(sid) ? collapsed.delete(sid) : collapsed.add(sid);
        render();
      });
    }

    const icon = el('span', { class: 'tree-icon', text: ICONS[pt] });
    const nameEl = el('span', { class: 'tree-name', text: s.span_name || s.operation_name || sid.slice(0, 16) });
    nameEl.title = s.span_name || '';
    const badge = el('span', { class: `tree-badge ${BADGE_CLASS[pt]}`, text: BADGE_LABEL[pt] });

    row.appendChild(toggle);
    row.appendChild(icon);
    row.appendChild(nameEl);
    row.appendChild(badge);
    node.appendChild(row);
    node.addEventListener('click', () => selectSpan(sid));
    wrap.appendChild(node);

    if (kids.length > 0 && !collapsed.has(sid)) {
      const ch = el('div', { class: 'tree-children' });
      kids.forEach(cid => ch.appendChild(buildTreeNode(cid, depth + 1, filter)));
      wrap.appendChild(ch);
    }
    return wrap;
  }

  // ── graph ───────────────────────────────────────────────────────────────────

  const NODE_W = 170, NODE_H = 48, X_GAP = 60, Y_GAP = 18;

  function buildGraphContainer() {
    const div = el('div', { class: 'graph-panel' });
    div.id = 'graph-container';
    return div;
  }

  function renderGraph() {
    const container = document.getElementById('graph-container');
    if (!container) return;

    // Assign levels (BFS depth)
    const level = {};
    const queue = [...roots];
    roots.forEach(r => { level[r] = 0; });
    const visited = new Set(roots);
    while (queue.length > 0) {
      const id = queue.shift();
      (childMap[id] || []).forEach(cid => {
        if (!visited.has(cid)) {
          level[cid] = (level[id] || 0) + 1;
          visited.add(cid);
          queue.push(cid);
        }
      });
    });
    // Spans not in tree (orphans)
    SPANS.forEach(s => {
      if (level[s.span_id] === undefined) level[s.span_id] = 0;
    });

    // Group by level
    const byLevel = {};
    SPANS.forEach(s => {
      const lv = level[s.span_id] || 0;
      if (!byLevel[lv]) byLevel[lv] = [];
      byLevel[lv].push(s);
    });

    const maxLevel = Math.max(...Object.keys(byLevel).map(Number));
    const maxPerLevel = Math.max(...Object.values(byLevel).map(a => a.length));

    const W = (maxLevel + 1) * (NODE_W + X_GAP) + X_GAP;
    const H = maxPerLevel * (NODE_H + Y_GAP) + Y_GAP + 20;

    // Assign positions
    const pos = {};
    Object.entries(byLevel).forEach(([lv, spans]) => {
      const x = parseInt(lv) * (NODE_W + X_GAP) + X_GAP;
      spans.forEach((s, i) => {
        pos[s.span_id] = { x, y: Y_GAP + i * (NODE_H + Y_GAP) };
      });
    });

    // Build SVG
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('width', W);
    svg.setAttribute('height', Math.max(H, 200));
    svg.setAttribute('class', 'graph');

    // Draw edges first (behind nodes)
    SPANS.forEach(s => {
      const pid = s.parent_span_id;
      if (pid && pos[pid] && pos[s.span_id]) {
        const p  = pos[pid];
        const c  = pos[s.span_id];
        const x1 = p.x + NODE_W;
        const y1 = p.y + NODE_H / 2;
        const x2 = c.x;
        const y2 = c.y + NODE_H / 2;
        const mx = (x1 + x2) / 2;
        const path = document.createElementNS(svgNS, 'path');
        path.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
        path.setAttribute('stroke', '#2d3f55');
        path.setAttribute('stroke-width', '1.5');
        path.setAttribute('fill', 'none');
        path.setAttribute('marker-end', 'url(#arrow)');
        svg.appendChild(path);
      }
    });

    // Arrow marker
    const defs = document.createElementNS(svgNS, 'defs');
    const marker = document.createElementNS(svgNS, 'marker');
    marker.setAttribute('id', 'arrow');
    marker.setAttribute('markerWidth', '8'); marker.setAttribute('markerHeight', '8');
    marker.setAttribute('refX', '6'); marker.setAttribute('refY', '3');
    marker.setAttribute('orient', 'auto');
    const arrow = document.createElementNS(svgNS, 'path');
    arrow.setAttribute('d', 'M0,0 L0,6 L8,3 z');
    arrow.setAttribute('fill', '#2d3f55');
    marker.appendChild(arrow);
    defs.appendChild(marker);
    svg.insertBefore(defs, svg.firstChild);

    // Draw nodes
    SPANS.forEach(s => {
      const p = pos[s.span_id];
      if (!p) return;
      const pt = ptypeOf(s);
      const color = COLORS[pt];
      const name = s.span_name || s.operation_name || s.span_id.slice(0, 12);
      const sub  = BADGE_LABEL[pt];
      const isSelected = selectedId === s.span_id;

      const g = document.createElementNS(svgNS, 'g');
      g.setAttribute('class', `gnode ${isSelected ? 'selected' : ''}`);
      g.setAttribute('transform', `translate(${p.x},${p.y})`);
      g.style.cursor = 'pointer';

      const rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('width', NODE_W);
      rect.setAttribute('height', NODE_H);
      rect.setAttribute('rx', 8);
      rect.setAttribute('fill', color + '22');
      rect.setAttribute('stroke', isSelected ? color : color + '66');
      rect.setAttribute('stroke-width', isSelected ? '2.5' : '1.5');
      g.appendChild(rect);

      const t1 = document.createElementNS(svgNS, 'text');
      t1.setAttribute('x', NODE_W / 2); t1.setAttribute('y', 18);
      t1.setAttribute('text-anchor', 'middle');
      t1.setAttribute('font-size', '11'); t1.setAttribute('fill', '#e2e8f0');
      t1.setAttribute('font-family', 'system-ui, sans-serif');
      t1.textContent = name.length > 20 ? name.slice(0, 18) + '…' : name;
      g.appendChild(t1);

      const t2 = document.createElementNS(svgNS, 'text');
      t2.setAttribute('x', NODE_W / 2); t2.setAttribute('y', 34);
      t2.setAttribute('text-anchor', 'middle');
      t2.setAttribute('font-size', '10'); t2.setAttribute('fill', color);
      t2.setAttribute('font-family', 'system-ui, sans-serif'); t2.setAttribute('font-weight', '700');
      t2.textContent = sub;
      g.appendChild(t2);

      g.addEventListener('click', () => selectSpan(s.span_id));
      svg.appendChild(g);
    });

    container.innerHTML = '';
    container.appendChild(svg);
  }

  // ── detail panel ─────────────────────────────────────────────────────────────

  function buildDetailPanel() {
    const panel = el('div', { class: 'detail-panel' });
    const hdr   = el('div', { class: 'detail-header' });
    const body  = el('div', { class: 'detail-body' });

    if (!selectedId || !spanMap[selectedId]) {
      hdr.appendChild(el('div', { class: 'detail-title', text: 'Span Details' }));
      hdr.appendChild(el('div', { class: 'detail-sub', text: 'Click any span to inspect it' }));
      body.appendChild(el('div', { class: 'detail-empty', text: 'Select a span from the tree or graph' }));
    } else {
      const s = spanMap[selectedId];
      const pt = ptypeOf(s);
      const color = COLORS[pt];
      hdr.appendChild(el('div', { class: 'detail-title', text: s.span_name || s.span_id }));
      hdr.appendChild(el('div', { class: 'detail-sub', text: s.span_id }));
      hdr.style.borderLeft = `4px solid ${color}`;

      // Basic info section
      body.appendChild(buildDetailSection('Span Metadata', [
        ['span_id',        s.span_id],
        ['trace_id',       s.trace_id],
        ['parent_span_id', s.parent_span_id || null],
        ['span_name',      s.span_name],
        ['span_type',      s.span_type],
        ['operation_name', s.operation_name],
        ['service_name',   s.service_name],
        ['status',         s.status],
        ['error_message',  s.error_message || null],
        ['started_at',     fmtTime(s.started_at)],
        ['completed_at',   fmtTime(s.completed_at)],
        ['duration_ms',    fmtDur(s.duration_ms)],
        ['created_at',     fmtTime(s.created_at)],
      ]));

      // Primitive classification
      const pdRows = [['primitive_type', s.primitive_type || null]];
      const pd = s.primitive_data || {};
      Object.entries(pd).forEach(([k, v]) => pdRows.push([k, v]));
      body.appendChild(buildDetailSection('Primitive Classification', pdRows));

      // Children
      const kids = childMap[s.span_id] || [];
      if (kids.length > 0) {
        body.appendChild(buildDetailSection('Children', kids.map(cid => {
          const c = spanMap[cid];
          return [c ? (c.span_name || cid.slice(0, 16)) : cid, c ? (c.primitive_type || 'unclassified') : '-'];
        })));
      }

      // Raw metadata_json
      if (s.metadata_json && Object.keys(s.metadata_json).length > 0) {
        const sec = el('div', { class: 'detail-section' });
        sec.appendChild(el('div', { class: 'detail-section-title', text: 'Raw metadata_json' }));
        const pre = el('div', { class: 'json-block' });
        pre.innerHTML = syntaxHighlight(JSON.stringify(s.metadata_json, null, 2));
        sec.appendChild(pre);
        body.appendChild(sec);
      }

      // Full raw span JSON
      const rawSec = el('div', { class: 'detail-section' });
      rawSec.appendChild(el('div', { class: 'detail-section-title', text: 'Full Span JSON' }));
      const rawPre = el('div', { class: 'json-block' });
      rawPre.innerHTML = syntaxHighlight(JSON.stringify(s, null, 2));
      rawSec.appendChild(rawPre);
      body.appendChild(rawSec);
    }

    panel.appendChild(hdr);
    panel.appendChild(body);
    return panel;
  }

  function buildDetailSection(title, rows) {
    const sec = el('div', { class: 'detail-section' });
    sec.appendChild(el('div', { class: 'detail-section-title', text: title }));
    rows.forEach(([k, v]) => {
      const row = el('div', { class: 'detail-row' });
      row.appendChild(el('div', { class: 'detail-key', text: k }));
      const valEl = el('div', { class: `detail-val${v === null || v === undefined ? ' null' : ''}` });
      if (v === null || v === undefined) {
        valEl.textContent = 'null';
      } else if (typeof v === 'object') {
        valEl.textContent = JSON.stringify(v);
      } else {
        valEl.textContent = String(v);
      }
      row.appendChild(valEl);
      sec.appendChild(row);
    });
    return sec;
  }

  // ── selection ────────────────────────────────────────────────────────────────

  function selectSpan(id) {
    selectedId = selectedId === id ? null : id;
    render();
  }

  // ── utils ────────────────────────────────────────────────────────────────────

  function el(tag, opts = {}) {
    const e = document.createElement(tag);
    if (opts.class) e.className = opts.class;
    if (opts.text)  e.textContent = opts.text;
    return e;
  }

  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function syntaxHighlight(json) {
    return esc(json).replace(
      /("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
      function(m) {
        let cls = 'json-num';
        if (/^"/.test(m)) {
          cls = /:$/.test(m) ? 'json-key' : 'json-str';
        } else if (/true|false/.test(m)) {
          cls = 'json-bool';
        } else if (/null/.test(m)) {
          cls = 'json-null';
        }
        return '<span class="' + cls + '">' + m + '</span>';
      }
    );
  }

  // ── boot ────────────────────────────────────────────────────────────────────
  render();

  } catch(e) {
    document.body.style.background = '#1a0000';
    document.body.innerHTML = '<div style="color:#ff6b6b;font-family:monospace;padding:24px;white-space:pre-wrap">'
      + '<b>Render error — please report this:</b>\n\n' + e.stack + '</div>';
  }
})();
</script>
</body>
</html>
"""
