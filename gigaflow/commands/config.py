"""config command (show / clear)."""

import json
import sys

from gigaflow import _config, _fmt


def register(sub) -> None:
    cfg  = sub.add_parser("config", help="Manage CLI configuration")
    csub = cfg.add_subparsers(dest="action", metavar="<action>")
    csub.add_parser("show",  help="Show saved configuration").set_defaults(func=_handle_show)
    csub.add_parser("clear", help="Clear saved configuration").set_defaults(func=_handle_clear)
    cfg.set_defaults(func=_handle_no_action)


def _handle_no_action(args, base_url: str) -> None:
    # argparse has no sub-action — print help
    import argparse
    argparse.ArgumentParser(prog="gigaflow config").parse_args(["--help"])


def _handle_show(args, base_url: str) -> None:
    config = _config.load()
    if not config:
        print(f"  No configuration found at {_config.CONFIG_PATH}")
        print("  Run:  gigaflow setup")
    else:
        print()
        print(json.dumps(config, indent=2))
    print()


def _handle_clear(args, base_url: str) -> None:
    _config.clear()
    _fmt.ok(f"Configuration cleared ({_config.CONFIG_PATH})")
    print()
