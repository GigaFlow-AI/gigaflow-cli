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
