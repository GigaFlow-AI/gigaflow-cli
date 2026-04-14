# Querying GigaFlow data

GigaFlow exposes all trace and AIF data through a single flat view, `trace_metrics`, queryable from the CLI or any SQL client.

The typical workflow is:

```
gigaflow query "<SQL>"    →  explore data, build a target set
gigaflow compute "<SQL>"  →  run AIF on that set
gigaflow query "<SQL>"    →  inspect results
```

---

## `gigaflow query`

Runs a SQL SELECT against the `trace_metrics` view via the backend API.

```bash
gigaflow query "SELECT trace_name, groundedness, tool_consumption FROM trace_metrics WHERE run_id IS NOT NULL"
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` | 1000 | Max rows returned (hard cap 5000) |
| `--format table\|csv\|json` | `table` | Output format |
| `--file PATH` | — | Read SQL from a file instead |
| `--schema` | — | Print the column reference and exit |
| `--examples` | — | Print example queries and exit |

**Constraints:** Only `SELECT` is allowed, and only against `trace_metrics`. Any DML or DDL is rejected by the backend.

When results include a `trace_id` column, the CLI automatically prints the equivalent `gigaflow compute` invocation.

---

## `gigaflow compute`

Batch-runs AIF analysis on every trace returned by a SQL query. The query must return a `trace_id` column.

```bash
gigaflow compute "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--force` | off | Recompute even for traces that already have results |
| `--concurrency N` | 3 | Parallel trace workers |
| `--model MODEL` | backend default | LLM used for atomisation |
| `--k-threshold K` | 0.3 | Relevance threshold for `@K` metrics (0.0–1.0) |

**Requires:** `OPENAI_API_KEY` in environment or `gigaflow.env`.

Traces with existing AIF results are skipped automatically. Use `--force` to recompute.

---

## `trace_metrics` view

One row per trace. AIF columns are `NULL` until `gigaflow compute` has been run for that trace.

### Identity

| Column | Type | Description |
|--------|------|-------------|
| `trace_id` | uuid | Primary key |
| `project_id` | uuid | Owning project |
| `source_trace_id` | text | Original ID in the upstream datasource |
| `trace_name` | text | |
| `service_name` | text | |
| `environment` | text | e.g. `prod`, `staging`, `dev` |

### Timing

| Column | Type | Description |
|--------|------|-------------|
| `started_at` | timestamptz | |
| `completed_at` | timestamptz | |
| `duration_ms` | float | |
| `trace_status` | text | `completed`, `failed`, `running` |
| `trace_created_at` | timestamptz | When gigaflow ingested the trace |

### Span counts

| Column | Type | Description |
|--------|------|-------------|
| `span_count` | int | Total spans |
| `primitive_span_count` | int | Spans with a classified primitive type |
| `llm_call_count` | int | |
| `tool_invocation_count` | int | |
| `user_input_count` | int | |
| `transform_count` | int | |
| `error_span_count` | int | Spans with `status = 'failed'` |

### Token usage

Summed from `llm_call` spans. `NULL` if no `llm_call` spans in the trace.

| Column | Type |
|--------|------|
| `total_tokens` | int |
| `total_prompt_tokens` | int |
| `total_completion_tokens` | int |

### AIF run (latest run per trace)

`NULL` on all of these if AIF has not been computed.

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | uuid | `NULL` = not yet computed |
| `aif_model` | text | LLM used for atomisation |
| `k_threshold` | float | Relevance threshold used |
| `query` | text | Extracted user query from the trace |
| `run_created_at` | timestamptz | |

### AIF metrics

`NULL` until `gigaflow compute` is run. All are floats in [0.0, 1.0] except the `jsonb` per-tool columns.

| Column | Type | Description |
|--------|------|-------------|
| `groundedness` | float | RAP — fraction of response atoms backed by tool output |
| `tool_consumption` | float | RAR — fraction of tool output atoms cited in response |
| `groundedness_at_k` | float | RAP filtered to atoms with relevance ≥ `k_threshold` |
| `tool_consumption_at_k` | float | RAR filtered to atoms with relevance ≥ `k_threshold` |
| `tool_contribution` | jsonb | Per-tool TUP `{span_name: float}` |
| `tool_usage` | jsonb | Per-tool TUR `{span_name: float}` |
| `tool_contribution_at_k` | jsonb | Per-tool TUP @K |
| `tool_usage_at_k` | jsonb | Per-tool TUR @K |

---

## Example queries

**All traces without AIF results** (typical input for `gigaflow compute`):
```sql
SELECT trace_id FROM trace_metrics WHERE run_id IS NULL
```

**All traces with AIF results:**
```sql
SELECT trace_name, groundedness, tool_consumption, span_count
  FROM trace_metrics WHERE run_id IS NOT NULL
```

**Low groundedness in production** — model answers not backed by tool output:
```sql
SELECT trace_id, trace_name, groundedness, tool_consumption
  FROM trace_metrics
  WHERE groundedness < 0.4 AND environment = 'prod' AND run_id IS NOT NULL
  ORDER BY groundedness
```

**High tool count but low tool consumption** — tools called but results ignored:
```sql
SELECT trace_id, trace_name, tool_invocation_count, tool_consumption
  FROM trace_metrics
  WHERE tool_invocation_count > 3 AND tool_consumption < 0.3
    AND run_id IS NOT NULL
```

**Per-service aggregate metrics:**
```sql
SELECT service_name,
       COUNT(*) AS traces,
       ROUND(AVG(groundedness)::numeric, 3)      AS avg_groundedness,
       ROUND(AVG(tool_consumption)::numeric, 3)  AS avg_tool_consumption
  FROM trace_metrics WHERE run_id IS NOT NULL
  GROUP BY service_name ORDER BY traces DESC
```

**Compare environments:**
```sql
SELECT environment,
       COUNT(*) AS traces,
       ROUND(AVG(groundedness)::numeric, 3) AS avg_groundedness
  FROM trace_metrics WHERE run_id IS NOT NULL
  GROUP BY environment
```

**Slow traces with low groundedness:**
```sql
SELECT trace_name, duration_ms, groundedness
  FROM trace_metrics WHERE duration_ms > 10000 AND groundedness < 0.5
```

**Token-heavy traces:**
```sql
SELECT trace_name, total_tokens, llm_call_count, groundedness
  FROM trace_metrics WHERE total_tokens IS NOT NULL
  ORDER BY total_tokens DESC LIMIT 20
```

---

## Pipe query results into compute

Any query that returns `trace_id` can be passed directly to `gigaflow compute`:

```bash
# Compute AIF for all un-analysed traces
gigaflow compute "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"

# Recompute only the low-groundedness traces in prod
gigaflow compute "SELECT trace_id FROM trace_metrics WHERE groundedness < 0.4 AND environment = 'prod'"

# Recompute everything (overwrite existing results)
gigaflow compute "SELECT trace_id FROM trace_metrics" --force
```

---

## Direct database access

`gigaflow query` is rate-limited and SELECT-only. For BI tools, dashboards, or complex joins against raw tables (`spans`, `aif_atoms`, etc.), connect directly to Postgres:

```bash
psql $GIGAFLOW_DB_URL

# Inspect the view definition
\d trace_metrics

# Full schema
\dt
```

The `GIGAFLOW_DB_URL` environment variable is set in `gigaflow.env` when the backend is running via Docker Compose.
