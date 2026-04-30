# GigaFlow CLI

Connect an Arize Phoenix tracing database to GigaFlow and query your traces from the terminal.

## Installation

From source (development):
```bash
cd cli
pip install -e .
```

Once published to PyPI:
```bash
pip install gigaflow
```

## Quick start

```bash
# 1. First-time setup: walks through connecting Arize Phoenix to GigaFlow
gigaflow setup

# 2. Query your traces (auto-syncs from Arize first)
gigaflow query traces
gigaflow query spans <trace_id>
```

Configuration is saved to `~/.gigaflow/config.json` after setup, so you only run it once.

---

## Commands

### `gigaflow setup`

Interactive wizard that:
1. Verifies the GigaFlow backend is reachable
2. Creates a project
3. Uploads the default Arize Phoenix transform config
4. Prompts for your Arize PostgreSQL connection details
5. Registers the datasource and runs the first sync

To find your Arize DB port (it's randomly assigned by Docker):
```bash
docker compose port db 5432
# e.g. 0.0.0.0:61219->5432/tcp  →  use port 61219
```

If GigaFlow is running in Docker (the default), use `host.docker.internal` as the host.

---

### `gigaflow sync`

Re-syncs traces from the configured Arize datasource into GigaFlow.

```bash
gigaflow sync
```

---

### `gigaflow query traces`

Lists all traces for the configured project. Auto-syncs first.

```bash
gigaflow query traces
gigaflow query traces --no-sync   # skip sync for faster results
```

Example output:
```
── Traces ──────────────────────────────────────────────────────

  TRACE ID    NAME                       STATUS     STARTED AT
  ─────────   ────────────────────────   ────────   ───────────────────
  acf9004b…   arize-phoenix-project      completed  2026-03-12 20:33:34
  b20cc460…   arize-phoenix-project      completed  2026-03-12 20:34:59

  18 trace(s)
```

---

### `gigaflow query spans <trace_id>`

Lists spans for a specific trace with primitive classification. Auto-syncs first.

```bash
gigaflow query spans acf9004b-7fff-47a3-aaac-c0fae89cb0e9
gigaflow query spans acf9004b-7fff-47a3-aaac-c0fae89cb0e9 --no-sync
```

Example output:
```
── Spans for trace acf9004b… ───────────────────────────────────

  SPAN NAME              TYPE     PRIMITIVE        DETAIL               STARTED AT
  ────────────────────   ──────   ──────────────   ──────────────────   ───────────────────
  research_agent_query   SERVER   -                -                    2026-03-12 20:33:34
  analyze_query          CLIENT   llm_call         gpt-4 / 18 tok       2026-03-12 20:33:34
  web_search             CLIENT   tool_invocation  web_search           2026-03-12 20:33:35
  synthesize_results     CLIENT   llm_call         gpt-4 / 156 tok      2026-03-12 20:33:35
  format_response        INTERN   user_input       format_response      2026-03-12 20:33:35
  validate_input         INTERN   -                -                    2026-03-12 20:33:34

  6 span(s) — 4 classified, 2 unclassified
```

---

### `gigaflow query projects`

Lists all projects in GigaFlow.

```bash
gigaflow query projects
```

---

### `gigaflow query datasources`

Lists all registered datasources.

```bash
gigaflow query datasources
```

---

### `gigaflow config show`

Prints the current saved configuration.

```bash
gigaflow config show
# {
#   "backend_url": "http://localhost:8000/api/v1",
#   "project_id": "acf9004b-...",
#   "datasource_id": "586cb406-..."
# }
```

---

### `gigaflow config clear`

Clears the saved configuration. Run this before `gigaflow setup` to reconfigure.

```bash
gigaflow config clear
```

---

## Options

| Option | Applies to | Description |
|--------|-----------|-------------|
| `--backend URL` | all commands | Override the GigaFlow API base URL |
| `--no-sync` | `query` commands | Skip the auto-sync before querying |

The `--backend` flag takes precedence over the saved config. Useful for pointing at a non-default backend:
```bash
gigaflow --backend http://prod-server:8000/api/v1 query traces
```

---

## Configuration file

Stored at `~/.gigaflow/config.json`:

```json
{
  "backend_url": "http://localhost:8000/api/v1",
  "project_id": "acf9004b-7fff-47a3-aaac-c0fae89cb0e9",
  "datasource_id": "586cb406-00c6-4ffd-8fa8-782eefd8143f"
}
```

---

## API docs

Once the GigaFlow backend is running, the full REST API is browseable at:
```
http://localhost:8000/api/v1/docs
```

---

## Transform config (`transform.yml`)

A transform config tells GigaFlow how to classify and extract data from your tracing source. During `gigaflow setup` you can provide a custom one, or use the built-in Arize Phoenix default.

### Structure

```yaml
version: "1.0"          # required, always "1.0"
source: my_tracer       # a label for the tracing source (e.g. "arize_phoenix")

primitives:
  <primitive_type>:     # the name you give this span class (e.g. "llm_call")
    filter:
      field: <path>     # dot-notation path to the field to match on
      value: <string>   # the value that field must equal
    mapping:
      <gigaflow_field>: <path>   # extract this source path into a GigaFlow field
```

### Primitives

Each entry under `primitives` defines a **span class**. When GigaFlow ingests a trace, every span is tested against each primitive's `filter` in order. The first match wins and determines the span's `primitive_type`. Unmatched spans are stored with `primitive_type: null`.

You can define any primitive names you like. The three built-in ones GigaFlow understands for display and querying are:

| Primitive type | Meaning |
|---|---|
| `llm_call` | A call to a language model |
| `tool_invocation` | A tool or function call made by the agent |
| `user_input` | The top-level user-facing entry point (e.g. a chain or agent root) |

Custom names are valid and will be stored — they just won't have special display treatment yet.

### Filter

The `filter` block matches spans by a single field/value pair.

```yaml
filter:
  field: span_kind      # field to inspect (dot-notation, see Path resolution below)
  value: "LLM"          # must match exactly (string comparison)
```

Only one filter per primitive is supported. The first primitive whose filter matches a span wins.

### Mapping

The `mapping` block extracts values from the matched span into GigaFlow's `primitive_data` store. Keys are GigaFlow field names; values are dot-notation paths into the raw span.

```yaml
mapping:
  model: attributes.llm.model_name       # stored as primitive_data.model
  prompt_tokens: attributes.llm.token_count.prompt
  completion_tokens: attributes.llm.token_count.completion
  completion: attributes.output.value
```

Any field name is valid on the left-hand side. Only paths that resolve to a non-null value are included in `primitive_data`.

### Path resolution

Paths use dot-notation and support array indexing. They are resolved safely — a missing key at any level returns `null` rather than an error.

| Pattern | Example | Resolves to |
|---|---|---|
| Top-level field | `span_kind` | `span["span_kind"]` |
| Nested field | `attributes.llm.model_name` | `span["attributes"]["llm"]["model_name"]` |
| Array index | `messages[0].content` | `span["messages"][0]["content"]` |
| Missing key | `attributes.x.y` (where `x` is absent) | `null` |

### Full example

```yaml
version: "1.0"
source: arize_phoenix

primitives:
  llm_call:
    filter:
      field: span_kind
      value: "LLM"
    mapping:
      model: attributes.llm.model_name
      completion: attributes.output.value
      prompt_tokens: attributes.llm.token_count.prompt
      completion_tokens: attributes.llm.token_count.completion

  tool_invocation:
    filter:
      field: span_kind
      value: "TOOL"
    mapping:
      tool_name: attributes.tool.name
      tool_output: attributes.output.value

  user_input:
    filter:
      field: span_kind
      value: "CHAIN"
    mapping:
      content: attributes.input.value
```

### Tips

- **Filter on whatever field is most stable.** `span_kind` works well for Arize Phoenix. For other sources you might filter on `attributes.span.type`, `kind`, etc.
- **Mapping fields are optional.** If a path doesn't exist in a span, that field is silently omitted from `primitive_data` — it won't cause an error.
- **Order matters for filtering.** If two primitives could match the same span (e.g. both filter on `span_kind`), the one listed first wins.
- **You can re-run setup** with a new transform file at any time using `gigaflow config clear && gigaflow setup`.

---

## Authoritativeness mappings (`auth_mappings`)

`auth_mappings` is an **optional** top-level block in `transform.yml` that tags each tool-output atom with a per-atom *trust score*. The score is a Beta-distribution `(α, β)` pseudo-count pair — `α` accumulates "trustworthy" evidence, `β` accumulates "untrustworthy" evidence. GigaFlow propagates these scores along the attribution graph and rolls them up into the `authoritative_groundedness` trace-level metric.

If you don't author an `auth_mappings` block, every atom keeps the uniform prior `(1.0, 1.0)` ("no evidence either way") and `authoritative_groundedness` is reported as `null` — old metrics like `groundedness` and `transitive_groundedness` are completely unaffected.

The full design lives in [`docs/superpowers/specs/2026-04-22-tool-authoritativeness-design.md`](https://github.com/GigaFlow-AI/gigaflow/blob/main/docs/superpowers/specs/2026-04-22-tool-authoritativeness-design.md) in the main GigaFlow repo. This section is the user-facing how-to.

### When to use it

- **Retrieval / RAG tools.** You have a per-document signal (retrieval rank, BM25 score, vector-similarity score) you want to surface as trust. Higher-rank docs contribute more `α`; tail-of-list docs contribute more `β`.
- **HTTP tools.** You want 5xx responses to count as untrustworthy evidence (`β`); 2xx responses with the expected schema to count as trustworthy (`α`).
- **SQL tools.** Empty result sets or schema-violating rows accumulate `β`; well-formed rows accumulate `α`.
- **Any tool whose output has a "this part is more reliable than that part" signal you can express as a YAML rule.**

If your tool either always returns the same kind of result or you don't have a per-call quality signal, skip `auth_mappings` — uniform `(1, 1)` is fine and `authoritative_groundedness` will return `null` rather than a misleading number.

### Where it goes in `transform.yml`

`auth_mappings` is a **top-level key**, sibling to `version`, `source`, and `primitives`. It is **keyed by tool span name** (the `span_name` after GigaFlow's `tool_name` promotion — see "Span-name promotion" below).

```yaml
version: "1.0"
source: arize_phoenix

primitives:
  # ... your primitive definitions ...

auth_mappings:
  <tool_span_name>:           # e.g. "lookup", "search", "http_get"
    per_item_source: <key>    # optional — see "Per-item atomization" below
    rules:
      - when: "<expression>"
        contribution: { alpha: <float>, beta: <float> }
      - when: "<expression>"
        contribution: { alpha: <float>, beta: <float> }
      # ... more rules; first match wins ...
```

### Schema reference

| Field | Type | Required | Meaning |
|---|---|---|---|
| `auth_mappings` | `map[string, AuthMapping]` | optional | Top-level block. Keys are tool span names. Tools with no entry leave their atoms at the uniform prior. |
| `AuthMapping.per_item_source` | string \| null | optional | Key under `primitive_data` that holds a list of per-item records (e.g. `"documents"` for retrievers). When set, the atomizer splits this list and produces one atom per item, stamping each atom's `auth_source_meta` from the corresponding item. When unset, the tool output is atomized as one blob and all resulting atoms share tool-level metadata. |
| `AuthMapping.rules` | list of `AuthMappingRule` | required (may be empty) | Ordered list of rules. **First matching rule wins** — order rules from most specific to most general. |
| `AuthMappingRule.when` | string | required | Boolean expression evaluated in a safe-AST sandbox. See "The `when` expression grammar" below for what's allowed. |
| `AuthMappingRule.contribution` | `{alpha: float, beta: float}` | required | Pseudo-counts added to the matching atom's `(α, β)`. Both must be `≥ 0.0`. The uniform prior is `(1.0, 1.0)`, so `{alpha: 4.0, beta: 0.0}` produces `(5.0, 1.0)` — Beta(5, 1) mean ≈ 0.83. |

### Per-item atomization (`per_item_source`)

If your tool returns a *list* of items (e.g. a retriever returning a list of documents) and each item has its own per-doc signal, set `per_item_source` to the `primitive_data` key holding that list. The atomizer then creates **one atom per item** instead of one atom per tool call, and stamps each atom's `auth_source_meta` from the corresponding item.

Example: a retriever whose `primitive_data` shape is `{"documents": [{"title": ..., "rank": 1, "score": 0.92}, ...]}`:

```yaml
auth_mappings:
  search:
    per_item_source: documents       # split primitive_data.documents into per-item atoms
    rules:
      - when: "meta.rank == 1"
        contribution: { alpha: 4.0, beta: 0.0 }
      - when: "meta.rank == 2"
        contribution: { alpha: 2.0, beta: 1.0 }
      - when: "meta.rank >= 3"
        contribution: { alpha: 0.0, beta: 3.0 }
```

When `per_item_source` is omitted (or the tool doesn't expose a list-of-items shape), all atoms produced from a span share the *tool-level* context. `meta` resolves to an empty dict in this case — only `primitive_data.*` references will work in `when` expressions.

### The `when` expression grammar

`when` is a restricted Python-like expression evaluated in a safe-AST sandbox. Two variables are in scope:

- `meta` — the current atom's `auth_source_meta` dict (the per-item metadata when `per_item_source` is set; an empty dict otherwise).
- `primitive_data` — the tool span's `primitive_data` dict (whatever your `mapping` block extracted).

#### Allowed constructs

| Construct | Example |
|---|---|
| Comparisons | `meta.rank == 1`, `primitive_data.status >= 500`, `meta.score > 0.8` |
| Chained comparisons | `0.5 <= meta.score < 0.8` |
| Boolean operators | `meta.rank == 1 and meta.score > 0.7`, `primitive_data.error or primitive_data.timeout` |
| Negation | `not primitive_data.error` |
| Attribute access (nested) | `primitive_data.response.status_code` |
| Constants | numbers, strings, `True`, `False`, `None` |

#### Refused (safety)

Function calls, list/dict subscripts, comprehensions, lambdas, dunder access (`__class__`, `__subclasses__`, etc.), arithmetic operators (`+`, `-`, `*`, `/`), and any AST node not on the allowlist all raise an error at config-load time. This keeps the rule grammar narrow enough to safely evaluate against untrusted trace data.

#### Missing-key semantics

A reference to a missing attribute (e.g. `meta.rank` when the atom has no `auth_source_meta`) does **not** raise — it silently evaluates as falsy. So `meta.rank == 1` returns `False` cleanly when `meta` is empty; `not primitive_data.error` returns `True` when `primitive_data` has no `error` key. This means you can write rules without worrying about every tool call having every field.

### Span-name promotion

`auth_mappings` is keyed by the **effective `span_name`** stored in GigaFlow's DB, *after* `tool_name` promotion. For `tool_invocation` primitives, GigaFlow's transformer promotes the value at `mapping.tool_name` (e.g. `attributes.gen_ai.tool.name`) into `span_name`. So if your mapping has:

```yaml
primitives:
  tool_invocation:
    filter: { field: span_name, value: "running tool", mode: prefix }
    mapping:
      tool_name: attributes.gen_ai.tool.name      # → "lookup"
      tool_output: attributes.tool_response
```

and the underlying span has `attributes.gen_ai.tool.name == "lookup"`, the `auth_mappings` key is `"lookup"` (not `"running tool: lookup"`). When in doubt, run a sync, then check `SELECT span_name FROM spans WHERE primitive_type = 'tool_invocation' LIMIT 5` — that's the value GigaFlow will look up.

### Worked example 1 — HotpotQA-style retrieval (no per-doc list)

Each `lookup` tool call returns one Wikipedia article (no list to split). Use a single catch-all rule that gives every successful lookup a moderate positive prior; let embedding corroboration (the second authoritativeness signal source, automatic) differentiate gold docs from distractors:

```yaml
version: "1.0"
source: logfire

primitives:
  # ... user_input / llm_call / tool_invocation ...

auth_mappings:
  lookup:
    # No per_item_source — each span is one article, not a list.
    rules:
      - when: "primitive_data.tool_output"
        contribution: { alpha: 2.0, beta: 1.0 }   # Beta(3, 2) mean ≈ 0.60
```

This is the actual mapping shipped in `benchmarks/hotpot_qa/transform.yml` in the main GigaFlow repo.

### Worked example 2 — retriever with per-document rank

A search tool whose `primitive_data` shape is `{"documents": [{"rank": 1, "score": 0.92, ...}, {"rank": 2, ...}, ...]}`:

```yaml
auth_mappings:
  search:
    per_item_source: documents
    rules:
      # Rules listed most-specific to most-general; first match wins.
      - when: "meta.rank == 1 and meta.score > 0.8"
        contribution: { alpha: 5.0, beta: 0.0 }    # very trusted
      - when: "meta.rank == 1"
        contribution: { alpha: 3.0, beta: 0.0 }    # top hit, lower score
      - when: "meta.rank <= 3"
        contribution: { alpha: 1.0, beta: 0.0 }    # mid-list
      - when: "meta.rank >= 4"
        contribution: { alpha: 0.0, beta: 2.0 }    # tail; treat as untrusted
```

### Worked example 3 — HTTP tool with status-code reliability

An HTTP fetch tool whose `primitive_data` includes the response status:

```yaml
auth_mappings:
  http_get:
    # No per-item list — one atom per call.
    rules:
      - when: "primitive_data.status >= 500"
        contribution: { alpha: 0.0, beta: 5.0 }    # 5xx → strong untrustworthy evidence
      - when: "primitive_data.status >= 400"
        contribution: { alpha: 0.0, beta: 2.0 }    # 4xx → moderate untrustworthy
      - when: "primitive_data.status == 200"
        contribution: { alpha: 2.0, beta: 0.0 }    # 200 OK → moderate trustworthy
      - when: "primitive_data.error"
        contribution: { alpha: 0.0, beta: 5.0 }    # transport / parse error
```

### How to debug

1. **Confirm your tool's `span_name` after promotion.** Run `gigaflow query "SELECT DISTINCT span_name FROM spans WHERE primitive_type = 'tool_invocation'"` — the keys in `auth_mappings` must match exactly.
2. **Run AIF on a known trace.** `gigaflow compute "SELECT trace_id FROM trace_metrics WHERE trace_id = '<your-id>'" --force`.
3. **Inspect the per-atom `(α, β)` stamped on tool-output atoms:**

   ```bash
   gigaflow query "SELECT
     atom_id,
     primitive_id,
     content,
     metadata->>'auth_alpha' AS auth_alpha,
     metadata->>'auth_beta'  AS auth_beta,
     metadata->'auth_source_meta' AS source_meta
   FROM aif_atoms
   WHERE run_id = '<run-id>'
     AND primitive_type = 'tool_invocation'
     AND origin_field = 'output'
   ORDER BY atom_id"
   ```

   - All atoms at `(1.0, 1.0)` → either no rule fired, the tool's `span_name` doesn't match an `auth_mappings` key, or every rule's `when` evaluated false.
   - `auth_source_meta` is `null` on every atom → `per_item_source` either isn't set or doesn't resolve to a list under `primitive_data`.
   - Rules firing with unexpected `(α, β)` → check rule order; the **first matching rule wins**, so a broad rule above a narrow one will mask the narrow one.
4. **Check the rolled-up metric.** `authoritative_groundedness` appears in the AIF viewer's Metrics tab and in `trace_metrics`. `null` means the trace was scored but no atom was stamped above the prior — usually a sign that no `auth_mappings` rule fired.
5. **Backend logs.** A malformed `when` expression (e.g. an arithmetic op or function call) raises at config-load time; an invalid runtime comparison (e.g. comparing a missing key) is logged as a warning and treated as a non-match. Look for `auth-rule eval failed at runtime` in the backend logs to spot rules that are silently failing.

### Defaults and bounds

- `α` and `β` defaults: each contribution must be `≥ 0.0`. The atom's running `(α, β)` starts at the uniform prior `(1.0, 1.0)` and is incremented by the first matching rule's contribution.
- Atoms not produced by `tool_invocation/output` (LLM responses, user input, tool inputs) are **never** scored by `auth_mappings` — they stay at `(1.0, 1.0)`. Authoritativeness is a tool-output property; downstream propagation handles the rest.
- A trace where no atom was stamped above the prior reports `authoritative_groundedness = null`, distinct from `0.25` (which is what the prior would otherwise propagate to). Dashboards should treat `null` as "unscored," not "low-trust."
