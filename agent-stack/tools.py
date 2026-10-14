"""HTTP client for the tool service. Contains NO tool implementation.

One generic entry point, call(name, **kwargs), so adding a tool on the server
never requires editing a wrapper here (no duplicated tool list to drift).
"""
import os
import time

import requests


class ToolServiceError(RuntimeError):
    """The service answered, but with an error (bad args, unknown tool, 5xx)."""


class ToolServiceUnavailable(ToolServiceError):
    """The service could not be reached at all (down, DNS, timeout)."""


def _url():
    return os.environ.get("MCP_URL", "http://mcp-server:8080").rstrip("/")


def _headers():
    token = os.environ.get("MCP_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _timeout():
    # Must be larger than the server's own inner timeout (5s for the vendor
    # call), so a slow vendor comes back as {"ok": false} instead of a
    # client-side timeout that hides the real reason.
    return float(os.environ.get("MCP_TIMEOUT", "15"))


def _error_text(resp):
    try:
        return resp.json().get("error", resp.text)
    except ValueError:
        return resp.text[:200]


def call(name, **kwargs):
    try:
        resp = requests.post(
            f"{_url()}/tools/{name}", json=kwargs, headers=_headers(), timeout=_timeout()
        )
    except requests.RequestException as e:
        raise ToolServiceUnavailable(f"{name}: {e}") from e
    if resp.status_code >= 400:
        raise ToolServiceError(f"{name}: HTTP {resp.status_code}: {_error_text(resp)}")
    return resp.json()["result"]


def list_tools():
    """Discover tools and their JSON schemas from the service."""
    try:
        resp = requests.get(f"{_url()}/tools", headers=_headers(), timeout=_timeout())
    except requests.RequestException as e:
        raise ToolServiceUnavailable(str(e)) from e
    if resp.status_code >= 400:
        raise ToolServiceError(f"GET /tools: HTTP {resp.status_code}: {_error_text(resp)}")
    return {t["name"]: t for t in resp.json()["tools"]}


def wait_until_ready(required=(), timeout=30.0):
    """Preflight: wait for /health, then verify every required tool exists.

    Raises ToolServiceUnavailable if the service never comes up, and
    ToolServiceError if it is up but lacks a tool or rejects our token.
    """
    deadline = time.monotonic() + timeout
    last = "no attempt"
    while True:
        try:
            requests.get(f"{_url()}/health", timeout=3).raise_for_status()
            break
        except requests.RequestException as e:
            last = str(e)
        if time.monotonic() >= deadline:
            raise ToolServiceUnavailable(f"tool service not ready after {timeout}s: {last}")
        time.sleep(1)
    available = list_tools()
    missing = [t for t in required if t not in available]
    if missing:
        raise ToolServiceError(f"tool service is missing required tools: {missing}")
    return available
