# gigaflow

Find out what your agent actually used.

GigaFlow traces every claim in your agent's responses back to the tool calls and context that grounded them. If a tool ran but never influenced the output, or if tokens were consumed without contributing to any response, you'll see it.

```bash
pip install gigaflow
```

---

## What you get

### Attribution graph at the claim level
GigaFlow breaks your agent's responses into individual claims and traces each one back to the tool outputs, retrieved documents, or context that supported it. You can see exactly which parts of your pipeline are doing work — and which aren't.

### Token waste detection
Not every token your agent consumes ends up mattering. GigaFlow identifies tokens that were passed into LLM calls but had no measurable influence on the output. These are candidates for prompt pruning, context window optimization, or retrieval tuning.

### Tool waste detection
Every tool call that ran but contributed nothing to the final response is flagged. Whether it's a retrieval step that returned irrelevant results or a function that ran redundantly, GigaFlow surfaces tool calls that are costing you latency and money without earning their keep.

---

## Quickstart

**1. Connect to your Arize Phoenix instance**

```bash
gigaflow setup
```

This registers your project with the GigaFlow backend, connects to your Arize Phoenix Postgres database, and stores your config at `~/.gigaflow/config.json`.

**2. Sync traces**

```bash
gigaflow traces
```

Auto-syncs your traces from Phoenix on first run. After that, use `gigaflow sync` to pull in new traces.

**3. Run analysis on a trace**

```bash
gigaflow aif run <trace_id>
```

Opens the attribution graph in your browser. To run with a specific model or inspect intermediate atoms:

```bash
gigaflow aif run <trace_id> --model gpt-4o --show-atoms
```

---

## All commands

```bash
gigaflow setup                   # Connect to Arize Phoenix and configure your project
gigaflow traces                  # List traces (auto-syncs on first run)
gigaflow spans <trace_id>        # Inspect spans for a trace
gigaflow aif run <trace_id>      # Run attribution analysis and open the viewer
gigaflow sync                    # Pull new traces from your datasource
gigaflow config show             # Show saved config
gigaflow config clear            # Reset config
```

---

## Custom span mappings

GigaFlow ships with a built-in mapping for Arize Phoenix's OpenInference schema. If your setup uses non-standard attribute paths, you can provide a custom `transform.yml` during setup:

```
Path to transform.yml (leave blank for built-in Arize Phoenix config): /path/to/my_transform.yml
```

After changing the mapping, re-sync to reclassify your spans:

```bash
gigaflow sync
```

The transform config maps raw span columns to three primitive types GigaFlow understands — `llm_call`, `tool_invocation`, and `user_input`:

```yaml
version: "1.0"
source: arize_phoenix

primitives:
  llm_call:
    filter:
      field: span_kind
      value: "LLM"
    mapping:
      completion: attributes.output.value
      model: attributes.llm.model_name

  tool_invocation:
    filter:
      field: span_kind
      value: "TOOL"
    mapping:
      tool_output: attributes.output.value
      tool_name: attributes.tool.name

  user_input:
    filter:
      field: span_kind
      value: "CHAIN"
    mapping:
      content: attributes.input.value
```

Mapping values are dot-notation paths into each span row. Nested JSON columns like `attributes` are parsed automatically.

---

## Requirements

- Python 3.10+
- A running [GigaFlow backend](https://github.com/GigaFlow-AI/gigaflow)
- An [Arize Phoenix](https://github.com/Arize-ai/phoenix) instance with a Postgres backend
