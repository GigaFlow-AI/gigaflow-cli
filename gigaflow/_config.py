"""Persistent CLI configuration stored in ~/.gigaflow/config.json."""

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


def clear():
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
