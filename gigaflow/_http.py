"""Minimal HTTP client (stdlib only).

Adds optional bearer-token auth, a request timeout, and retry-with-backoff for
idempotent requests / connection errors — while preserving the original return
contract:

    (status, payload)

where ``status`` is the HTTP status code on a real response, or ``None`` when
the backend could not be reached at all (connection refused, DNS failure,
timeout). Callers throughout the CLI test for ``status is None`` / ``status !=
200``, so that sentinel is kept intact.
"""

import json
import time
import urllib.error
import urllib.request

# Per-request timeout (seconds). Without this urllib can hang indefinitely when
# a hosted backend is slow or a connection silently stalls.
DEFAULT_TIMEOUT = 30.0

# Idempotent verbs that are safe to retry after a transient connection failure.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Retry policy (connection-level failures only — never HTTP error responses).
_MAX_TRIES = 3
_BACKOFF_BASE = 0.5  # seconds; doubled each retry: 0.5, 1.0, ...


def api(
    base_url: str,
    method: str,
    path: str,
    body=None,
    content_type: str = "application/json",
    api_key: str | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Make an HTTP request to the gigaflow backend.

    Returns ``(status, payload)``. ``status`` is ``None`` on a connection-level
    failure (backend unreachable). When ``api_key`` is set an
    ``Authorization: Bearer <api_key>`` header is added; extra ``headers`` are
    merged in and override the defaults.

    Idempotent requests (GET/HEAD/OPTIONS) are retried up to ``_MAX_TRIES`` times
    with exponential backoff on connection errors. HTTP error responses
    (4xx/5xx) are returned as-is and never retried, so auth failures surface
    immediately.
    """
    data = None
    if body is not None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()

    method = method.upper()
    retryable = method in _IDEMPOTENT_METHODS

    last_reason = None
    for attempt in range(_MAX_TRIES):
        req = urllib.request.Request(f"{base_url}{path}", data=data, method=method)
        req.add_header("Content-Type", content_type)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # Real HTTP response — return it verbatim, never retry.
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"error": raw.decode()}
        except urllib.error.URLError as e:
            last_reason = str(e.reason)
        except TimeoutError as e:  # pragma: no cover - timeout race
            last_reason = str(e) or "request timed out"

        # Connection-level failure: retry idempotent requests with backoff.
        if not retryable or attempt == _MAX_TRIES - 1:
            break
        time.sleep(_BACKOFF_BASE * (2 ** attempt))

    return None, {"error": last_reason or "connection failed"}


def auth_error_hint() -> str:
    """One-line, actionable message for a 401/403 from the backend."""
    return (
        "Authentication failed — set GIGAFLOW_API_KEY, pass --api-key, "
        "or run 'gigaflow setup'."
    )


def unreachable_hint(base_url: str) -> str:
    """One-line, actionable message for an unreachable backend (status None)."""
    return (
        f"Could not reach the gigaflow backend at {base_url} — "
        "is GIGAFLOW_BACKEND_URL correct and the backend running?"
    )
