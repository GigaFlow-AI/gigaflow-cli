# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e .          # install CLI locally for development
uv run pytest             # run CLI tests
uv run ruff check .       # lint
```

## Local development cycle

The CLI is published as its own package (`gigaflow`) and used against a
running gigaflow backend. When a backend change (new endpoint, new field,
etc.) requires a matching CLI change, the loop looks like:

```bash
# One-time: editable install from the checkout
cd /path/to/gigaflow-cli
pip install -e .

# Point at a local backend via the normal setup flow
gigaflow setup                                 # enter http://localhost:8000 when prompted

# Iterate: edit source, rerun the command — no reinstall needed
$EDITOR gigaflow/commands/sync.py
gigaflow sync                                  # picks up your edits immediately
gigaflow query "select * from trace_metrics limit 5"
```

`pip install -e .` installs the `gigaflow` console script to your active
environment (venv / uv / system) with the source directory linked in, so
every edit is live on the next invocation. No rebuild, no reinstall.

**Coordinated change across repos:** when a single feature spans backend
and CLI, branch both repos, run the backend locally (`docker compose up`
in `gigaflow/`), and run the editable CLI against it. Merge the backend
PR first, then the CLI PR — the CLI is a thin HTTP client and defaults
to failing gracefully on missing endpoints.

**Publishing a release:** bump `version` in `pyproject.toml`, tag, and
push — `.github/workflows/publish.yml` handles the PyPI upload.

## Architecture

The CLI has **zero external dependencies** — stdlib only (`urllib`, `argparse`, `json`, `pathlib`, `concurrent.futures`). All HTTP calls to the backend use `urllib` directly.

**Config** is persisted in `~/.gigaflow/config.json` (backend URL, project ID, API key). `gigaflow.env` in the working directory is auto-loaded at startup via `set -a && source gigaflow.env`.

**Transform config resolution** — `gigaflow setup` prompts for a `transform.yml` path. If left blank, the built-in `gigaflow/transforms/arize_phoenix.yml` (bundled as package data) is used.

**Key commands:**
- `setup` — interactive first-run: registers project with backend, uploads transform config, stores datasource connection
- `sync` — queries source Phoenix Postgres directly, batches raw spans, POSTs to `/api/v1/datasources/{id}/sync`; skips traces already in the DB
- `query "<SQL>"` — run a SQL SELECT against the `trace_metrics` view; use `--examples` to print suggested patterns; `--format table|csv|json`; `--file` to read SQL from a file
- `compute "<SQL>"` — batch-compute AIF for traces returned by a SQL query; the query must return `trace_id`; skips traces with existing results unless `--force`; `--concurrency N` (default 3); `--model`; `--k-threshold`; `--attribution-mode {llm,embedding}` selects the attribution backend (omit to fall through to `settings.ATTRIBUTION_MODE`), with `--embedding-threshold` and `--embedding-model` packed into `embedding_config`; `--cost-breakdown` prints a per-stage (model, tokens, requests, USD) table after each run, in addition to the default one-line `cost: $X.XXXX` summary
- `inspect <trace_id>` — open the browser viewer for a trace; shows spans always, AIF tabs if results are available (prompts to compute if not)
- `supplement [SESSION_ID]` — enrich Claude Code OTLP spans with unredacted assistant text, thinking blocks, and full tool outputs by uploading the local `~/.claude/projects/<slug>/<session>.jsonl` file. `--latest` picks the most-recently-modified session JSONL; `--all <dir>` walks a whole project directory; `--session-file PATH` overrides the auto-lookup; `--with-subagents` synthesises child spans under parent Task tool_invocations (stretch); `--dry-run` reports without writing; `--force` re-supplements already-supplemented spans. The command gzip-compresses the JSONL body and POSTs it to `/api/v1/supplement/claude_code` so WSL2 volume-mount friction doesn't bite.

## Conventions

- Never add external dependencies — keep the install footprint at zero
- The CLI is a thin HTTP client; business logic lives in the backend
- AIF columns in `trace_metrics` are NULL until `gigaflow compute` is run for a trace
