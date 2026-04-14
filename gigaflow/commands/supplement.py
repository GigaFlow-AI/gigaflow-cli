"""supplement command — enrich Claude Code OTLP spans with local JSONL content.

Claude Code exports OTLP spans continuously while the user runs a session,
but its OTEL export unconditionally masks assistant response text and
thinking blocks, and caps tool output at 60 KB. This command takes the
local ``~/.claude/projects/<slug>/<session>.jsonl`` file that Claude Code
always writes, gzip-compresses it, and POSTs it to the gigaflow backend
which overlays the unredacted content onto the existing spans (matched
on ``prompt.id``).

Usage:
  gigaflow supplement <session-id>
  gigaflow supplement --latest
  gigaflow supplement <session-id> --with-subagents
  gigaflow supplement --latest --dry-run
  gigaflow supplement --session-file /path/to/session.jsonl <session-id>
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from gigaflow import _fmt

# ── argparse wiring ──────────────────────────────────────────────────────────


def register(sub) -> None:
    p = sub.add_parser(
        "supplement",
        help="Enrich Claude Code OTLP spans with content from local session JSONL files",
        description=(
            "POST a Claude Code session JSONL file to the gigaflow backend so "
            "assistant text, thinking blocks, and full tool outputs are overlaid "
            "onto the OTLP-ingested spans. Idempotent: re-running only touches "
            "spans that haven't been supplemented yet (unless --force)."
        ),
    )
    p.add_argument(
        "session_id",
        metavar="SESSION_ID",
        nargs="?",
        default=None,
        help="Claude Code session UUID. Omit when using --latest.",
    )
    p.add_argument(
        "--latest",
        action="store_true",
        help="Supplement the most recently modified JSONL file under ~/.claude/projects.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Treat SESSION_ID as a directory and supplement every *.jsonl inside it.",
    )
    p.add_argument(
        "--session-file",
        metavar="PATH",
        default=None,
        help="Override the JSONL path lookup (absolute path to a specific file).",
    )
    p.add_argument(
        "--with-subagents",
        action="store_true",
        help="Also synthesize child spans under Task tool_invocations from sub-agent JSONLs (stretch).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to the backend DB.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-supplement spans that are already marked supplemented.",
    )
    p.add_argument(
        "--project-id",
        default=None,
        help="Restrict the span update to a single project (UUID).",
    )
    p.set_defaults(func=_handle_supplement)


# ── helpers ──────────────────────────────────────────────────────────────────


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _find_latest_jsonl() -> Path | None:
    root = _claude_projects_dir()
    if not root.is_dir():
        return None
    candidates = [p for p in root.glob("*/*.jsonl") if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _find_by_session_id(session_id: str) -> Path | None:
    root = _claude_projects_dir()
    if not root.is_dir():
        return None
    for child in root.glob("*"):
        candidate = child / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate
    return None


def _session_id_from_filename(path: Path) -> str:
    return path.stem


def _post_supplement(
    base_url: str,
    session_id: str,
    file_path: Path,
    *,
    with_subagents: bool,
    dry_run: bool,
    force: bool,
    project_id: str | None,
) -> tuple[int | None, dict]:
    """POST a gzip-compressed JSONL body to /api/v1/supplement/claude_code."""
    body = gzip.compress(file_path.read_bytes())
    query = [
        f"session_id={urllib.parse.quote(session_id)}",
        f"include_subagents={'true' if with_subagents else 'false'}",
        f"dry_run={'true' if dry_run else 'false'}",
        f"force={'true' if force else 'false'}",
    ]
    if project_id:
        query.append(f"project_id={urllib.parse.quote(project_id)}")
    url = f"{base_url}/supplement/claude_code?{'&'.join(query)}"

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/gzip")
    req.add_header("Content-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")}
    except urllib.error.URLError as e:
        return None, {"error": str(e.reason)}


def _print_report(report: dict, *, path: Path) -> None:
    _fmt.section(f"Supplement report — {path.name}")
    print(f"  session_id           : {report.get('session_id')}")
    print(f"  bytes_ingested       : {report.get('bytes_ingested', 0):,}")
    print(f"  spans_matched        : {report.get('spans_matched', 0)}")
    print(f"  spans_updated        : {report.get('spans_updated', 0)}")
    print(f"  spans_not_matched    : {report.get('spans_not_matched', 0)}")
    print(f"  jsonl_rows_unused    : {report.get('jsonl_rows_unused', 0)}")
    print(f"  subagents_synthesized: {report.get('subagents_synthesized', 0)}")
    print(f"  assistant_text_chars : {report.get('assistant_text_chars', 0):,}")
    print(f"  thinking_chars       : {report.get('thinking_chars', 0):,}")
    print(f"  tool_output_chars    : {report.get('tool_output_chars', 0):,}")
    warnings = report.get("warnings") or []
    for w in warnings:
        _fmt.warn(w)
    errors = report.get("errors") or []
    for e in errors:
        _fmt.fail(e)


# ── handler ──────────────────────────────────────────────────────────────────


def _handle_supplement(args, base_url: str) -> None:
    _fmt.header("GigaFlow Supplement")

    # Strip the /api/v1 suffix if caller passed base_url without it — our
    # helper function adds /supplement/claude_code on top.
    # base_url is already e.g. http://host:8000/api/v1; keep as-is.

    # ── Resolve the list of (session_id, file_path) pairs to process ─────────
    jobs: list[tuple[str, Path]] = []

    if args.session_file:
        path = Path(args.session_file).expanduser().resolve()
        if not path.is_file():
            _fmt.fail(f"--session-file not found: {path}")
            sys.exit(1)
        sid = args.session_id or _session_id_from_filename(path)
        jobs.append((sid, path))

    elif args.all:
        if not args.session_id:
            _fmt.fail("--all requires a directory path as SESSION_ID")
            sys.exit(1)
        d = Path(args.session_id).expanduser().resolve()
        if not d.is_dir():
            _fmt.fail(f"--all expects a directory, got: {d}")
            sys.exit(1)
        for p in sorted(d.glob("*.jsonl")):
            jobs.append((_session_id_from_filename(p), p))
        if not jobs:
            _fmt.warn(f"No *.jsonl files under {d}")
            return

    elif args.latest:
        path = _find_latest_jsonl()
        if path is None:
            _fmt.fail("No JSONL files found under ~/.claude/projects/")
            sys.exit(1)
        jobs.append((_session_id_from_filename(path), path))

    elif args.session_id:
        path = _find_by_session_id(args.session_id)
        if path is None:
            _fmt.fail(
                f"Session {args.session_id} not found under ~/.claude/projects/. "
                "Pass --session-file to override the lookup."
            )
            sys.exit(1)
        jobs.append((args.session_id, path))

    else:
        _fmt.fail("Provide a SESSION_ID, --latest, --all <dir>, or --session-file")
        sys.exit(1)

    # ── Run each job ─────────────────────────────────────────────────────────
    failures = 0
    for sid, path in jobs:
        _fmt.info(f"Supplementing session {sid} from {path}")
        status, result = _post_supplement(
            base_url,
            session_id=sid,
            file_path=path,
            with_subagents=args.with_subagents,
            dry_run=args.dry_run,
            force=args.force,
            project_id=args.project_id,
        )
        if status != 200:
            detail = (result or {}).get("detail") or (result or {}).get("error") or result
            _fmt.fail(f"  supplement failed ({status}): {detail}")
            failures += 1
            continue
        _print_report(result, path=path)

    if failures:
        sys.exit(1)
