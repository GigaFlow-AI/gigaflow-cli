"""gigaflow query — run SQL against the trace_metrics view.

Primary use: discovery and exploration before running gigaflow compute.
For heavy analytics, connect to Postgres directly with psql / DBeaver / BI tools.
"""

import json
import sys

from gigaflow import _fmt
from gigaflow._http import api

# ── Schema reference ──────────────────────────────────────────────────────────

_SCHEMA = """
  trace_metrics — one row per trace
  ──────────────────────────────────
  For direct DB access: psql $GIGAFLOW_DB_URL -c "\\d trace_metrics"

  Identity
    trace_id          uuid        Primary key
    project_id        uuid        Owning project
    source_trace_id   text        Original ID in the upstream datasource
    trace_name        text
    service_name      text
    environment       text        e.g. prod, staging, dev

  Timing
    started_at        timestamptz
    completed_at      timestamptz
    duration_ms       float
    trace_status      text        completed | failed | running
    trace_created_at  timestamptz When gigaflow ingested the trace

  Span counts
    span_count              int   Total spans
    primitive_span_count    int   Spans with a gigaflow primitive type
    llm_call_count          int
    tool_invocation_count   int
    user_input_count        int
    transform_count         int
    error_span_count        int

  Token usage (summed from llm_call spans)
    total_tokens            int   NULL if no llm_call spans
    total_prompt_tokens     int
    total_completion_tokens int

  AIF run (latest run only; all NULL if AIF not yet computed)
    run_id            uuid        NULL = not yet computed
    aif_model         text        LLM used for atomisation
    k_threshold       float       Relevance threshold for @K metrics
    query             text        Extracted user query from the trace
    run_created_at    timestamptz

  AIF metrics (NULL until gigaflow compute is run)
    groundedness            float   RAP  — fraction of response atoms backed by tool output
    tool_consumption        float   RAR  — fraction of tool output atoms cited in response
    groundedness_at_k       float   RAP filtered to atoms with relevance ≥ k_threshold
    tool_consumption_at_k   float   RAR filtered to atoms with relevance ≥ k_threshold
    tool_contribution       jsonb   Per-tool TUP_t  {span_name: float}
    tool_usage              jsonb   Per-tool TUR_t  {span_name: float}
    tool_contribution_at_k  jsonb   Per-tool TUP_t @K
    tool_usage_at_k         jsonb   Per-tool TUR_t @K
"""

# ── Example queries ───────────────────────────────────────────────────────────

_EXAMPLES = """
  Example queries  (use these to explore, then feed into gigaflow compute)
  ─────────────────────────────────────────────────────────────────────────

  Traces not yet analysed — the typical input to gigaflow compute:
    SELECT trace_id FROM trace_metrics WHERE run_id IS NULL

  All traces with AIF results:
    SELECT trace_name, groundedness, tool_consumption, span_count
      FROM trace_metrics WHERE run_id IS NOT NULL

  Low groundedness in production — model answers not backed by tool output:
    SELECT trace_id, trace_name, groundedness, tool_consumption
      FROM trace_metrics
      WHERE groundedness < 0.4 AND environment = 'prod' AND run_id IS NOT NULL
      ORDER BY groundedness

  High tool count but low tool consumption — tools called but ignored:
    SELECT trace_id, trace_name, tool_invocation_count, tool_consumption
      FROM trace_metrics
      WHERE tool_invocation_count > 3 AND tool_consumption < 0.3
        AND run_id IS NOT NULL

  Per-service aggregate metrics:
    SELECT service_name, COUNT(*) AS traces,
           ROUND(AVG(groundedness)::numeric, 3) AS avg_groundedness,
           ROUND(AVG(tool_consumption)::numeric, 3) AS avg_tool_consumption
      FROM trace_metrics WHERE run_id IS NOT NULL
      GROUP BY service_name ORDER BY traces DESC

  Slow traces (> 10 s) with low groundedness:
    SELECT trace_name, duration_ms, groundedness
      FROM trace_metrics WHERE duration_ms > 10000 AND groundedness < 0.5

  Token-heavy traces:
    SELECT trace_name, total_tokens, llm_call_count, groundedness
      FROM trace_metrics WHERE total_tokens IS NOT NULL
      ORDER BY total_tokens DESC LIMIT 20

  Compare two environments:
    SELECT environment, COUNT(*) AS traces,
           ROUND(AVG(groundedness)::numeric, 3) AS avg_groundedness
      FROM trace_metrics WHERE run_id IS NOT NULL
      GROUP BY environment

  ──

  Once you have a query returning trace_id, pipe it into gigaflow compute:
    gigaflow compute "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"
    gigaflow compute "SELECT trace_id FROM trace_metrics WHERE groundedness < 0.4"

  For advanced analytics, connect directly to the Postgres DB:
    psql $GIGAFLOW_DB_URL -c "SELECT ..."
"""


def register(sub) -> None:
    p = sub.add_parser(
        "query",
        help="Explore trace_metrics with SQL (discovery tool — feeds into gigaflow compute)",
        description=(
            "Run a SQL SELECT against trace_metrics — a flat view joining traces, "
            "span counts, and the latest AIF results. Use this to explore your data "
            "and build queries for gigaflow compute.\n\n"
            "Only SELECT statements against trace_metrics are permitted. "
            "For advanced analytics, connect directly to the Postgres DB."
        ),
    )
    p.add_argument(
        "sql",
        nargs="?",
        default=None,
        help='SQL SELECT statement (e.g. "SELECT trace_name, groundedness FROM trace_metrics")',
    )
    p.add_argument("--file", metavar="PATH", help="Read SQL from a file instead")
    p.add_argument("--limit", type=int, default=1000, metavar="N", help="Max rows (default 1000)")
    p.add_argument(
        "--format",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p.add_argument(
        "--examples",
        action="store_true",
        help="Print example queries (including gigaflow compute follow-throughs) and exit",
    )
    p.add_argument(
        "--schema",
        action="store_true",
        help="Print the trace_metrics column reference and exit",
    )
    p.set_defaults(func=_handle_query)


# ── Handler ───────────────────────────────────────────────────────────────────

def _handle_query(args, base_url: str) -> None:
    if args.schema:
        print(_SCHEMA)
        return

    if args.examples:
        print(_EXAMPLES)
        return

    # Resolve SQL source
    sql = None
    if args.file:
        try:
            with open(args.file) as f:
                sql = f.read().strip()
        except OSError as e:
            _fmt.fail(f"Cannot read file {args.file!r}: {e}")
            sys.exit(1)
    elif args.sql:
        sql = args.sql.strip()

    if not sql:
        _fmt.fail(
            "Provide a SQL statement or --file.\n"
            "  Use --schema to see available columns.\n"
            "  Use --examples to see suggested queries."
        )
        sys.exit(1)

    status, resp = api(base_url, "POST", "/query/", body={"sql": sql, "limit": args.limit})

    if status != 200:
        detail = resp.get("detail", resp) if isinstance(resp, dict) else resp
        _fmt.fail(f"Query failed ({status}): {detail}")
        sys.exit(1)

    columns = resp.get("columns", [])
    rows = resp.get("rows", [])
    truncated = resp.get("truncated", False)
    row_count = resp.get("row_count", len(rows))

    if args.format == "json":
        print(json.dumps({"columns": columns, "rows": rows, "row_count": row_count, "truncated": truncated}, indent=2))
        return

    if args.format == "csv":
        print(",".join(columns))
        for row in rows:
            print(",".join("" if v is None else str(v) for v in row))
    else:  # table (default)
        _fmt.section("Query Results")
        _fmt.table(rows, [c.upper() for c in columns])

    print(f"  {row_count} row(s)", end="")
    if truncated:
        print("  (truncated — use --limit to increase)", end="")
    print()
    print()

    # If results include trace_id, suggest gigaflow compute
    if "trace_id" in columns and row_count > 0 and args.format == "table":
        _fmt.info(f"Tip: pipe these results into gigaflow compute:")
        print(f'  gigaflow compute "{sql}"')
        print()
