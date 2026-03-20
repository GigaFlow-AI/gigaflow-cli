"""Terminal formatting: headers, prompts, tables."""

import getpass
import sys

WIDTH = 62


def header(title: str):
    print()
    print("=" * WIDTH)
    print(f"  {title}")
    print("=" * WIDTH)


def section(title: str):
    pad = max(0, WIDTH - len(title) - 4)
    print(f"\n── {title} {'─' * pad}")


def ok(msg: str):
    print(f"  ✓  {msg}")


def fail(msg: str):
    print(f"  ✗  {msg}", file=sys.stderr)


def info(msg: str):
    print(f"     {msg}")


def prompt(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            val = input(f"  {label}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        result = val if val else default
        if result or not required:
            return result
        print(f"     {label} is required.")


def prompt_password(label: str) -> str:
    try:
        return getpass.getpass(f"  {label}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def table(rows: list, headers: list, max_col: int = 38):
    """Print a simple fixed-width table."""
    if not rows:
        print("  (no results)")
        return

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = min(max(widths[i], len(str(cell))), max_col)

    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "  " + "  ".join("─" * w for w in widths)

    print()
    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            s = str(cell) if cell is not None else "-"
            cells.append(s[: widths[i] - 1] + "…" if len(s) > widths[i] else s)
        print(fmt.format(*cells))
    print()
