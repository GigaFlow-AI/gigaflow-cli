# gigaflow CLI

Command-line interface for GigaFlow — connect Arize Phoenix traces to GigaFlow and analyze them with Atomic Information Flow (AIF).

## Install

```bash
pip install gigaflow
```

Or from source:

```bash
cd cli && pip install -e .
```

## Usage

```bash
gigaflow setup                                           # Connect to Arize Phoenix datasource
gigaflow traces                                          # List traces (auto-syncs first)
gigaflow spans <trace_id>                                # List spans for a trace
gigaflow aif run <trace_id>                              # Run AIF analysis
gigaflow aif run <trace_id> --model gpt-4o --show-atoms
gigaflow sync                                            # Re-sync from datasource
gigaflow config show                                     # Show saved config
gigaflow config clear                                    # Reset config
```

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
cd cli
pip install build twine
python -m build
twine upload dist/*
```
