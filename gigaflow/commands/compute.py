"""compute command — batch AIF computation over a SQL-selected set of traces.

Usage:
  gigaflow compute "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"
  gigaflow compute "SELECT trace_id FROM trace_metrics WHERE env = 'prod'" --force
  gigaflow compute "SELECT trace_id FROM trace_metrics" --concurrency 5 --model gpt-4o-mini
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from gigaflow import _fmt
from gigaflow._http import api

_HINT = (
    "Hint: query the `trace_metrics` view — it has one row per trace with all AIF columns.\n"
    "      Run `gigaflow query --examples` to see example queries."
)


def register(sub) -> None:
    p = sub.add_parser(
        "compute",
        help="Batch-compute AIF for traces matching a SQL query",
        description=(
            "Run AIF analysis on every trace returned by a SQL SELECT.\n\n"
            "The query must return a `trace_id` column. Traces that already have AIF\n"
            "results are skipped unless --force is given.\n\n"
            + _HINT
        ),
    )
    p.add_argument(
        "sql",
        metavar="SQL",
        help='SELECT returning trace_id, e.g. "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"',
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Recompute AIF even for traces that already have results",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Number of traces to process in parallel (default: 3)",
    )
    p.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="LLM model to use (default: backend default)",
    )
    p.add_argument(
        "--k-threshold",
        type=float,
        default=None,
        metavar="K",
        help="Relevance threshold for @K metrics, 0.0–1.0 (default: 0.3)",
    )
    p.add_argument(
        "--attribution-mode",
        choices=["llm", "embedding"],
        default=None,
        help="Attribution backend: 'llm' (default) or 'embedding'. Omit to use the backend's configured default.",
    )
    p.add_argument(
        "--embedding-threshold",
        type=float,
        default=None,
        metavar="T",
        help="Cosine-similarity cutoff for embedding-mode attribution (forwarded as embedding_config.threshold).",
    )
    p.add_argument(
        "--embedding-model",
        default=None,
        metavar="MODEL",
        help="Embedding model for embedding-mode attribution (forwarded as embedding_config.model).",
    )
    p.add_argument(
        "--cost-breakdown",
        action="store_true",
        help="After each run, print a per-stage cost table (model, tokens, requests, USD)",
    )
    p.set_defaults(func=_handle_compute)


# ── Handler ─────────────────────────────────────────────────────────────────

def _handle_compute(args, base_url: str) -> None:
    _fmt.header("GigaFlow Compute")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _fmt.fail(
            "OPENAI_API_KEY not set. Add it to gigaflow.env or export it before running."
        )
        sys.exit(1)

    # ── 1. Run the user's SQL to collect trace IDs ───────────────────────────
    _fmt.section("Selecting traces")
    status, result = api(base_url, "POST", "/query/", {"sql": args.sql, "limit": 5000})
    if status != 200:
        _fmt.fail(f"Query failed ({status}): {result}")
        print(f"\n{_HINT}")
        sys.exit(1)

    columns = result.get("columns", [])
    rows = result.get("rows", [])

    if "trace_id" not in columns:
        _fmt.fail(
            f"Query must return a `trace_id` column. Got: {columns or '(no columns)'}"
        )
        print(f"\n{_HINT}")
        sys.exit(1)

    tid_idx = columns.index("trace_id")
    all_ids = [row[tid_idx] for row in rows if row[tid_idx]]

    if not all_ids:
        _fmt.ok("Query returned 0 traces — nothing to do.")
        return

    if result.get("truncated"):
        _fmt.warn("Query returned more than 5000 rows — processing first 5000.")

    # ── 2. Filter out already-computed traces (unless --force) ───────────────
    if args.force:
        trace_ids = all_ids
        _fmt.info(f"--force: will recompute {len(trace_ids)} trace(s)")
    else:
        trace_ids, skipped = _partition_computed(base_url, all_ids)
        if skipped:
            _fmt.info(f"Skipping {len(skipped)} trace(s) with existing AIF results (use --force to recompute)")
        if not trace_ids:
            _fmt.ok("All traces already have AIF results — nothing to do.")
            return

    _fmt.section(f"Computing AIF for {len(trace_ids)} trace(s)  [concurrency={args.concurrency}]")

    # ── 3. Build the request body ────────────────────────────────────────────
    body: dict = {"api_key": api_key}
    if args.model:
        body["model"] = args.model
    if args.k_threshold is not None:
        body["k_threshold"] = args.k_threshold
    if args.attribution_mode:
        body["attribution_mode"] = args.attribution_mode
    embedding_config: dict = {}
    if args.embedding_threshold is not None:
        embedding_config["threshold"] = args.embedding_threshold
    if args.embedding_model:
        embedding_config["model"] = args.embedding_model
    if embedding_config:
        body["embedding_config"] = embedding_config

    # ── 4. Run in parallel ───────────────────────────────────────────────────
    success = 0
    failure = 0
    width = len(str(len(trace_ids)))

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(_run_one, base_url, tid, body): tid for tid in trace_ids}
        for i, future in enumerate(as_completed(futures), 1):
            tid = futures[future]
            short = tid[:8]
            try:
                groundedness, tool_consumption, token_usage = future.result()
                _fmt.ok(
                    f"[{i:{width}}/{len(trace_ids)}] {short}…  "
                    f"groundedness={groundedness:.2f}  tool_consumption={tool_consumption:.2f}"
                )
                # Per-trace cost summary: one-line default, full table with --cost-breakdown.
                _print_cost_summary(token_usage)
                if args.cost_breakdown:
                    _print_cost_breakdown(token_usage)
                success += 1
            except Exception as exc:
                _fmt.fail(f"[{i:{width}}/{len(trace_ids)}] {short}…  {exc}")
                failure += 1

    print()
    if failure == 0:
        _fmt.ok(f"Done — {success} trace(s) computed successfully.")
    else:
        _fmt.fail(f"Done — {success} succeeded, {failure} failed.")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _partition_computed(base_url: str, trace_ids: list[str]) -> tuple[list[str], list[str]]:
    """Return (to_compute, already_computed) by checking trace_metrics."""
    # Build a query to find which of the given IDs already have AIF (run_id IS NOT NULL)
    quoted = ", ".join(f"'{tid}'" for tid in trace_ids)
    sql = f"SELECT trace_id FROM trace_metrics WHERE trace_id IN ({quoted}) AND run_id IS NOT NULL"
    status, result = api(base_url, "POST", "/query/", {"sql": sql, "limit": 5000})
    if status != 200:
        # If check fails, be conservative and compute all
        return trace_ids, []

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    if "trace_id" not in columns:
        return trace_ids, []

    tid_idx = columns.index("trace_id")
    computed = {row[tid_idx] for row in rows}
    to_compute = [tid for tid in trace_ids if tid not in computed]
    already_done = [tid for tid in trace_ids if tid in computed]
    return to_compute, already_done


def _run_one(base_url: str, trace_id: str, body: dict) -> tuple[float, float, dict]:
    """Run AIF for a single trace. Returns (groundedness, tool_consumption, token_usage).

    ``token_usage`` is the raw dict as serialised by ``AIFTokenUsage.model_dump``:
    carries the per-stage breakdown, ``total_cost_usd``, and ``pricing_version``.
    Missing for old runs persisted before cost instrumentation shipped — the
    caller is expected to handle an empty dict gracefully.
    """
    status, resp = api(base_url, "POST", f"/aif/{trace_id}", body)
    if status != 200:
        detail = resp.get("detail", str(resp)) if isinstance(resp, dict) else str(resp)
        raise RuntimeError(detail)
    metrics = resp.get("metrics", {}) if isinstance(resp, dict) else {}
    token_usage = resp.get("token_usage") if isinstance(resp, dict) else None
    return (
        metrics.get("groundedness") or 0.0,
        metrics.get("tool_consumption") or 0.0,
        token_usage or {},
    )


# ── Cost printers ────────────────────────────────────────────────────────────


def _print_cost_summary(token_usage: dict) -> None:
    """Print the one-line cost summary attached to every computed trace.

    Format:
        cost:  $0.0428  (14,760 tokens across 37 requests)

    No-op when ``token_usage`` is empty or missing total_cost_usd — keeps
    old runs (pre-cost instrumentation) from crashing the output path.
    """
    if not token_usage:
        return
    total_cost = token_usage.get("total_cost_usd") or 0.0
    total_tokens = token_usage.get("total_tokens") or 0
    total_requests = token_usage.get("total_requests") or 0
    print(
        f"     cost:  ${total_cost:.4f}  "
        f"({total_tokens:,} tokens across {total_requests} requests)"
    )


def _print_cost_breakdown(token_usage: dict) -> None:
    """Print a per-stage cost table for one trace when --cost-breakdown is set.

    Example:
        atomization          gpt-4o-mini    9,500 tok   22 req   $0.0061   14%
        attribution          gpt-4o         4,580 tok   12 req   $0.0329   77%
        --
        total                              14,760 tok   37 req   $0.0428  100%
        pricing: 2026-04-13
    """
    if not token_usage:
        return
    stages = token_usage.get("stages") or {}
    if not stages:
        return

    total_cost = token_usage.get("total_cost_usd") or 0.0
    total_tokens = token_usage.get("total_tokens") or 0
    total_requests = token_usage.get("total_requests") or 0
    pricing_version = token_usage.get("pricing_version")

    # Sort stages by descending cost, with stage name as the stable tiebreaker
    # so runs with equal (e.g. all-zero unknown-model) costs render in the same
    # order every time. Dict insertion order would otherwise flap based on which
    # stage happened to record tokens first in the pipeline.
    ordered = sorted(
        stages.items(),
        key=lambda kv: (-float((kv[1] or {}).get("cost_usd") or 0.0), kv[0]),
    )

    def pct(value: float) -> str:
        if total_cost <= 0:
            return "  0%"
        return f"{(value / total_cost) * 100:3.0f}%"

    for name, stage in ordered:
        stage = stage or {}
        model = stage.get("model") or "unknown"
        tokens = stage.get("total_tokens") or 0
        requests = stage.get("requests") or 0
        cost = float(stage.get("cost_usd") or 0.0)
        # Widths: stage names cap at dsat_classification (19); canonical model
        # names cap at claude-3-5-sonnet / claude-sonnet-4-6 (17). Extra char of
        # slack keeps Anthropic models from shifting the downstream columns.
        print(
            f"     {name:<20} {model:<18} "
            f"{tokens:>6,} tok {requests:>4} req   ${cost:.4f}  {pct(cost)}"
        )

    print(f"     {'-' * 2}")
    print(
        f"     {'total':<20} {'':<18} "
        f"{total_tokens:>6,} tok {total_requests:>4} req   ${total_cost:.4f}  100%"
    )
    if pricing_version:
        print(f"     pricing: {pricing_version}")
