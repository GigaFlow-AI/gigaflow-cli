# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
cd cli
pip install -e .          # install CLI locally for development
uv run pytest             # run CLI tests
uv run ruff check .       # lint
```

## Architecture

The CLI has **zero external dependencies** — stdlib only (`urllib`, `argparse`, `json`, `pathlib`). All HTTP calls to the backend use `urllib` directly.

**Config** is persisted in `~/.gigaflow/config.json` (backend URL, project ID, API key). `gigaflow.env` in the working directory is auto-loaded at startup via `set -a && source gigaflow.env`.

**Transform config resolution** — `gigaflow setup` prompts for a `transform.yml` path. If left blank, the built-in `gigaflow/transforms/arize_phoenix.yml` (bundled as package data) is used.

**Key commands:**
- `setup` — interactive first-run: registers project with backend, uploads transform config, stores datasource connection
- `sync` — queries source Phoenix Postgres directly, batches raw spans, POSTs to `/api/v1/datasources/{id}/sync`; skips traces already in the DB
- `run aif` — lists traces, POSTs to `/api/v1/aif/{trace_id}`, opens the HTML viewer URL in the browser

## Conventions

- Never add external dependencies — keep the install footprint at zero
- The CLI is a thin HTTP client; business logic lives in the backend
