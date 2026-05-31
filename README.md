# gigaflow CLI

Command-line interface for GigaFlow — connect Arize Phoenix traces to GigaFlow and analyze them with Atomic Information Flow (AIF).

## Install

```bash
pip install gigaflow
```

Or from source:

```bash
git clone https://github.com/GigaFlow-AI/gigaflow-cli
cd gigaflow-cli && pip install -e .
```

## Usage

```bash
gigaflow setup                                           # First-run wizard: backend, datasource, transform
gigaflow traces                                          # List traces (auto-syncs first)
gigaflow spans <trace_id>                                # List spans for a trace
gigaflow query "SELECT * FROM trace_metrics LIMIT 5"     # Run SQL against the trace_metrics view
gigaflow compute "SELECT trace_id FROM trace_metrics"    # Batch-compute AIF for matching traces
gigaflow inspect <trace_id>                              # Open the browser viewer for a trace
gigaflow sync                                            # Re-sync from datasource
gigaflow config show                                     # Show saved config
gigaflow config clear                                    # Reset config
```

## Connect to a hosted GigaFlow backend

By default the CLI talks to a backend on `http://localhost:8000`. To point it at
a hosted GigaFlow backend, set the backend URL and (when the backend requires
one) an API key. You can supply these three ways — environment variables, flags,
or by running `gigaflow setup`.

### Quickstart (zero to AIF in minutes)

```bash
# 1. Install the CLI (stdlib-only, nothing else to pull in)
pip install gigaflow

# 2. Point at your hosted backend and authenticate
export GIGAFLOW_BACKEND_URL=https://api.gigaflow.ai/api/v1
export GIGAFLOW_API_KEY=gf_live_...           # the key from your GigaFlow account
export OPENAI_API_KEY=sk-...                   # used by `compute` for the AIF LLM calls

# 3. Run the first-run wizard: registers your project, uploads the transform
#    config, and connects your Arize Phoenix datasource
gigaflow setup

# 4. Pull traces from your datasource into GigaFlow
gigaflow sync

# 5. Compute AIF for every trace that doesn't have results yet
gigaflow compute "SELECT trace_id FROM trace_metrics WHERE run_id IS NULL"

# 6. Open a trace in the browser viewer (spans + AIF tabs)
gigaflow inspect <trace_id>
```

That's it — once `setup` has saved your config, the `GIGAFLOW_BACKEND_URL` /
`GIGAFLOW_API_KEY` exports are optional on later runs (they're persisted to
`~/.gigaflow/config.json`), though keeping them in your shell profile is fine.

### Configuring the backend URL and API key

Each value resolves in priority order — the first one set wins:

**Backend URL** — `--backend <url>` > `$GIGAFLOW_BACKEND_URL` > saved config
`backend_url` > `http://localhost:8000/api/v1`.

**API key** — `--api-key <key>` > `$GIGAFLOW_API_KEY` > saved config `api_key` >
none. When present it is forwarded on every request as
`Authorization: Bearer <key>`. The AIF compute endpoint (`POST /aif/{trace_id}`)
requires it whenever the backend runs with `GIGAFLOW_DEV_MODE=false`; a local
dev backend (`GIGAFLOW_DEV_MODE=true`) accepts requests without a key.

```bash
# Environment variables (persist across commands in your shell)
export GIGAFLOW_BACKEND_URL=https://api.gigaflow.ai/api/v1
export GIGAFLOW_API_KEY=gf_live_...
gigaflow projects

# Or per-invocation flags (override everything else)
gigaflow --backend https://api.gigaflow.ai/api/v1 --api-key gf_live_... projects
```

`gigaflow setup` also prompts for the backend URL (defaulting to the current
resolved value) and an optional API key, and saves both to
`~/.gigaflow/config.json` so you don't have to set them again.

> **Two different keys, two different places.** The `GIGAFLOW_API_KEY` above
> authenticates you to the GigaFlow backend and travels in the
> `Authorization: Bearer` header. `gigaflow compute` *separately* forwards your
> `OPENAI_API_KEY` (from the environment) in the request **body**, where the
> backend uses it to make the AIF LLM calls on your behalf. Set both.

If the backend is unreachable or rejects the key, the CLI prints a short,
actionable message instead of a traceback. Network requests use a 30 s timeout,
and idempotent GETs are retried up to three times with exponential backoff.

## Transform config

GigaFlow maps raw Arize Phoenix spans to its own primitives (`llm_call`, `tool_invocation`, `user_input`) using a YAML transform config.

The built-in config for Arize Phoenix is at:

```
gigaflow/transforms/arize_phoenix.yml
```

It maps the OpenInference span columns (`span_kind`, `attributes.*`) to the fields that AIF reads. If your Arize setup uses different attribute paths, copy the file, edit it, and provide the path during `gigaflow setup` when prompted:

```
Path to transform.yml (leave blank for built-in Arize Phoenix config): /path/to/my_transform.yml
```

You can also re-upload a transform config to an existing project without re-running the full wizard:

```bash
curl -X PUT http://localhost:8000/api/v1/projects/<project_id>/transform \
  -H "Content-Type: text/plain" \
  --data-binary @my_transform.yml
```

After changing the transform config, re-sync to reclassify your spans:

```bash
gigaflow sync
```

Advanced: a `transform.yml` may also declare an optional top-level `auth_mappings` block that tags each tool-output atom with a per-atom Beta-distribution trust score, surfaced as the `authoritative_groundedness` trace metric. See [`gigaflow/README.md` → Authoritativeness mappings](gigaflow/README.md#authoritativeness-mappings-auth_mappings) for the full schema, the `when`-expression grammar, and worked examples for retrieval / HTTP / SQL tools.

### Transform config format

```yaml
version: "1.0"
source: arize_phoenix

primitives:
  llm_call:
    filter:
      field: span_kind        # top-level DB column
      value: "LLM"
    mapping:
      completion: attributes.output.value          # read by AIF for response atoms
      model: attributes.llm.model_name

  tool_invocation:
    filter:
      field: span_kind
      value: "TOOL"
    mapping:
      tool_output: attributes.output.value         # read by AIF for tool atoms
      tool_name: attributes.tool.name

  user_input:
    filter:
      field: span_kind
      value: "CHAIN"
    mapping:
      content: attributes.input.value              # read by AIF for the user query
```

Mapping values are dot-notation paths traversed against each span row. Nested JSON columns (e.g. `attributes`) are parsed automatically.

## Publish to PyPI

```bash
pip install build twine
python -m build
twine upload dist/*
```
