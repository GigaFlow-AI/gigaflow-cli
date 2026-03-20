"""projects / datasources commands."""

import sys

from gigaflow import _fmt
from gigaflow._http import api


def register(sub) -> None:
    sub.add_parser("projects",    help="List all projects").set_defaults(func=_handle_projects)
    sub.add_parser("datasources", help="List all datasources").set_defaults(func=_handle_datasources)


def _handle_projects(args, base_url: str) -> None:
    _fmt.section("Projects")
    status, resp = api(base_url, "GET", "/projects/")
    if status != 200:
        _fmt.fail(f"Failed to list projects: {resp}")
        sys.exit(1)

    projects = resp.get("projects", [])
    rows = [
        [p.get("project_id", "")[:8] + "…", p.get("name", "-"), (p.get("created_at") or "")[:19].replace("T", " ")]
        for p in projects
    ]
    _fmt.table(rows, ["PROJECT ID", "NAME", "CREATED AT"])
    print(f"  {len(projects)} project(s)\n")


def _handle_datasources(args, base_url: str) -> None:
    _fmt.section("Datasources")
    status, resp = api(base_url, "GET", "/datasources/")
    if status != 200:
        _fmt.fail(f"Failed to list datasources: {resp}")
        sys.exit(1)

    datasources = resp.get("datasources", [])
    rows = [
        [
            d.get("datasource_id", "")[:8] + "…",
            d.get("name", "-"),
            d.get("source_table", "-"),
            (d.get("created_at") or "")[:19].replace("T", " "),
        ]
        for d in datasources
    ]
    _fmt.table(rows, ["DATASOURCE ID", "NAME", "TABLE", "CREATED AT"])
    print(f"  {len(datasources)} datasource(s)\n")
