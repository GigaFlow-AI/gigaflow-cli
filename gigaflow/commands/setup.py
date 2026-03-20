"""setup / sync commands."""

import sys

from gigaflow import _config, _fmt
from gigaflow._setup import do_sync, run_wizard


def register(sub) -> None:
    sub.add_parser("setup", help="Configure GigaFlow with an Arize Phoenix datasource").set_defaults(func=_handle_setup)
    sub.add_parser("sync",  help="Re-sync traces from the configured datasource").set_defaults(func=_handle_sync)


def _handle_setup(args, base_url: str) -> None:
    config = _config.load()
    if config.get("datasource_id"):
        print(f"  Already configured (project: {config.get('project_id', '?')[:8]}…)")
        print("  To reconfigure, run:  gigaflow config clear  then  gigaflow setup")
        print()
        return
    result = run_wizard(base_url)
    if result is None:
        sys.exit(1)
    _fmt.section("Next steps")
    print()
    print("  gigaflow traces")
    print("  gigaflow spans <trace_id>")
    print("  gigaflow sync")
    print(f"  {base_url.replace('/api/v1', '')}/api/v1/docs")
    print()


def _handle_sync(args, base_url: str) -> None:
    config = _config.load()
    if not config.get("datasource_id"):
        _fmt.fail("No configuration found. Run:  gigaflow setup")
        sys.exit(1)
    _fmt.header("GigaFlow Sync")
    _fmt.section("Syncing")
    result = do_sync(base_url, config["datasource_id"])
    if result is None:
        sys.exit(1)
    print()
