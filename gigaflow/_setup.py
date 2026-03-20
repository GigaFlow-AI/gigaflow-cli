"""Interactive setup wizard: project creation, transform upload, datasource registration, sync."""

import importlib.resources
import sys

from gigaflow import _config, _fmt
from gigaflow._http import api


def _load_default_transform() -> str:
    """Load the built-in Arize Phoenix transform config from the package."""
    ref = importlib.resources.files("gigaflow.transforms").joinpath("arize_phoenix.yml")
    return ref.read_text(encoding="utf-8")


def load_env_file(path: str) -> dict:
    """Parse a .env-style file and return key-value pairs.

    Supports comments (#), blank lines, and optionally quoted values.
    """
    env: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key:
                    env[key] = value
    except OSError as e:
        _fmt.fail(f"Could not read env file: {e}")
    return env

ARIZE_TRANSFORM_YAML = _load_default_transform()


def check_backend(base_url: str) -> bool:
    status, resp = api(base_url, "GET", "/health")
    if status is None:
        _fmt.fail(f"Could not reach gigaflow backend at {base_url}")
        _fmt.info("Make sure the backend is running:  cd backend && make run")
        return False
    if status != 200:
        _fmt.fail(f"Backend returned {status}: {resp}")
        return False
    _fmt.ok(f"Backend reachable at {base_url}")
    return True


def create_project(base_url: str, name: str) -> str | None:
    status, resp = api(base_url, "POST", "/projects/", {"name": name})
    if status != 200:
        _fmt.fail(f"Failed to create project ({status}): {resp}")
        return None
    project_id = resp["project_id"]
    _fmt.ok(f"Project created: {name}")
    _fmt.info(f"project_id: {project_id}")
    return project_id


def upload_transform(base_url: str, project_id: str, yaml_content: str = ARIZE_TRANSFORM_YAML) -> bool:
    status, resp = api(
        base_url, "PUT", f"/projects/{project_id}/transform",
        yaml_content, content_type="text/plain",
    )
    if status != 200:
        _fmt.fail(f"Failed to upload transform config ({status}): {resp}")
        return False
    primitives = list(resp.get("transform_config", {}).get("primitives", {}).keys())
    _fmt.ok("Transform config uploaded")
    _fmt.info(f"primitives: {', '.join(primitives)}")
    return True


def register_datasource(base_url: str, project_id: str, connection_url: str, source_table: str) -> str | None:
    status, resp = api(base_url, "POST", "/datasources/", {
        "project_id": project_id,
        "name": "arize-phoenix",
        "connection_url": connection_url,
        "source_table": source_table,
    })
    if status != 200:
        _fmt.fail(f"Failed to register datasource ({status}): {resp}")
        return None
    datasource_id = resp["datasource_id"]
    _fmt.ok("Datasource registered")
    _fmt.info(f"datasource_id: {datasource_id}")
    return datasource_id


def do_sync(base_url: str, datasource_id: str) -> tuple[int, int] | None:
    status, resp = api(base_url, "POST", f"/datasources/{datasource_id}/sync")
    if status != 200:
        _fmt.fail(f"Sync failed ({status}): {resp.get('detail', resp)}")
        detail = str(resp.get("detail", ""))
        if "connect" in detail.lower() or status == 502:
            _fmt.info("Could not connect to the source database.")
            _fmt.info("If Arize is running in Docker, try 'host.docker.internal' as the host.")
        return None
    synced_traces = resp.get("synced_traces", 0)
    synced_spans = resp.get("synced_spans", 0)
    _fmt.ok(f"Sync complete: {synced_traces} trace(s), {synced_spans} span(s)")
    return synced_traces, synced_spans


def run_wizard(base_url: str) -> dict | None:
    """
    Interactive wizard. Returns saved config dict on success, None on failure.
    """
    _fmt.header("GigaFlow Setup Wizard")

    # ── Env file (optional) ───────────────────────────────────────────────────
    env_path = _fmt.prompt("Path to gigaflow.env (leave blank to enter values manually)")
    if env_path:
        env = load_env_file(env_path)
        if env:
            _fmt.ok(f"Loaded env file: {env_path}")
    else:
        env = {}

    # ── Step 1: backend ───────────────────────────────────────────────────────
    _fmt.section("Step 1: GigaFlow backend")
    if not check_backend(base_url):
        return None

    # ── Step 2: project ───────────────────────────────────────────────────────
    _fmt.section("Step 2: Project")
    project_name = _fmt.prompt("Project name", env.get("GIGAFLOW_PROJECT_NAME", "arize-phoenix-project"))
    project_id = create_project(base_url, project_name)
    if not project_id:
        return None

    transform_path = _fmt.prompt(
        "Path to transform.yml (leave blank for built-in Arize Phoenix config)",
        env.get("GIGAFLOW_TRANSFORM_YML", ""),
    )
    if transform_path:
        try:
            with open(transform_path) as f:
                yaml_content = f.read()
            _fmt.ok(f"Loaded transform file: {transform_path}")
        except OSError as e:
            _fmt.fail(f"Could not read transform file: {e}")
            return None
    else:
        yaml_content = ARIZE_TRANSFORM_YAML
        _fmt.info("Using built-in Arize Phoenix transform config")

    if not upload_transform(base_url, project_id, yaml_content):
        return None

    # ── Step 3: Arize Phoenix DB ──────────────────────────────────────────────
    _fmt.section("Step 3: Arize Phoenix database")
    print()
    print("  Enter the connection details for the PostgreSQL database")
    print("  that Arize Phoenix writes to.")
    print()
    print("  Tip: if GigaFlow is running in Docker (the default),")
    print("  use 'host.docker.internal' to reach the host machine.")
    print()
    print("  Find the Arize DB port with:")
    print("    docker ps --filter name=arize_agent_example-db --format '{{.Ports}}'")
    print()

    host  = _fmt.prompt("Host", env.get("GIGAFLOW_DB_HOST", "host.docker.internal"))
    port  = _fmt.prompt("Port", env.get("GIGAFLOW_DB_PORT", ""), required=True)
    user  = _fmt.prompt("User", env.get("GIGAFLOW_DB_USER", "postgres"))

    if env.get("GIGAFLOW_DB_PASSWORD"):
        password = env["GIGAFLOW_DB_PASSWORD"]
        _fmt.info("Password: [from env file]")
    else:
        password = _fmt.prompt_password("Password")

    db    = _fmt.prompt("Database",     env.get("GIGAFLOW_DB_NAME", "postgres"))
    table = _fmt.prompt("Source table", env.get("GIGAFLOW_DB_TABLE", "spans"))

    connection_url = f"postgresql://{user}:{password}@{host}:{port}/{db}"

    # ── Step 4: register + sync ───────────────────────────────────────────────
    _fmt.section("Step 4: Register datasource & sync")
    datasource_id = register_datasource(base_url, project_id, connection_url, table)
    if not datasource_id:
        return None

    result = do_sync(base_url, datasource_id)
    if result is None:
        return None

    synced_traces, _ = result
    if synced_traces > 0:
        _show_span_preview(base_url, project_id)

    config: dict = {
        "backend_url": base_url,
        "project_id": project_id,
        "datasource_id": datasource_id,
    }
    _config.save(config)
    _fmt.ok(f"Configuration saved to {_config.CONFIG_PATH}")
    return config


def _show_span_preview(base_url: str, project_id: str):
    status, resp = api(base_url, "GET", f"/traces/?project_id={project_id}")
    if status != 200:
        return
    traces = resp.get("traces", [])
    if not traces:
        return
    trace_id = traces[0]["trace_id"]
    status, resp = api(base_url, "GET", f"/traces/{trace_id}/spans")
    if status != 200:
        return
    spans = resp if isinstance(resp, list) else resp.get("spans", [])
    classified = [s for s in spans if s.get("primitive_type")]
    unclassified = [s for s in spans if not s.get("primitive_type")]
    _fmt.info(f"Sample trace — {len(classified)} classified, {len(unclassified)} unclassified")
    for ptype in ["llm_call", "tool_invocation", "user_input"]:
        match = next((s for s in spans if s.get("primitive_type") == ptype), None)
        if match:
            pd = match.get("primitive_data") or {}
            _fmt.info(f"  {ptype}: {pd}")
