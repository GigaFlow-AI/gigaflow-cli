"""Persistent CLI configuration stored in ~/.gigaflow/config.json.

Recognised keys (the file is a flat dict; helpers are generic so any key can be
stored, but these are the ones the CLI reads):

  backend_url    Hosted/local backend base URL (e.g. https://api.../api/v1)
  api_key        gigaflow API key, forwarded as "Authorization: Bearer <key>"
  project_id     Default project for traces/datasource/sync
  datasource_id  Default datasource for sync/traces
"""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".gigaflow" / "config.json"


def load() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get(key: str, default=None):
    """Read a single config value (None/`default` if unset)."""
    return load().get(key, default)


def set(key: str, value) -> dict:
    """Persist a single config value, preserving the rest of the file."""
    config = load()
    config[key] = value
    save(config)
    return config


def clear():
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
