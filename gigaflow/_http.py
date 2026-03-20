"""Minimal HTTP client (stdlib only)."""

import json
import urllib.error
import urllib.request


def api(base_url: str, method: str, path: str, body=None, content_type: str = "application/json"):
    data = None
    if body is not None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    req = urllib.request.Request(f"{base_url}{path}", data=data, method=method)
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode()}
    except urllib.error.URLError as e:
        return None, {"error": str(e.reason)}
