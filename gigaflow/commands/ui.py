"""ui command — open the Gigaflow traces dashboard in the browser."""

import sys

from gigaflow import _config, _fmt
from gigaflow.commands.inspect import _open_browser


def register(sub) -> None:
    p = sub.add_parser(
        "ui",
        help="Open the Gigaflow traces dashboard in the browser",
    )
    p.add_argument("--no-browser", action="store_true", help="Print URL without opening browser")
    p.set_defaults(func=_handle_ui)


def _handle_ui(args, base_url: str) -> None:
    cfg = _config.load()
    if not cfg.get("datasource_id"):
        _fmt.fail("No configuration found. Run:  gigaflow setup")
        sys.exit(1)

    ui_url = base_url.replace("/api/v1", "").rstrip("/") + "/"
    _fmt.info(f"Dashboard: {ui_url}")
    if not args.no_browser:
        _open_browser(ui_url)
