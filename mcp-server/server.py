"""Tool service: an MCP-style HTTP service (JSON in, JSON out).

NOT the official MCP protocol (no JSON-RPC / SDK transport). It keeps the
same architectural shape: tool discovery with JSON schemas, validated calls.

  GET  /health          no auth   liveness + version + tool names
  GET  /tools           auth      discovery: name, description, input schema
  POST /tools/<name>    auth      call a tool with a JSON object of arguments
"""
import hmac
import logging
import os

from flask import Flask, jsonify, request

import tools_impl

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tool-service")

VERSION = os.environ.get("APP_VERSION", "dev")

TOOLS = {
    "parse_manifest": {
        "fn": tools_impl.parse_manifest,
        "description": "Parse raw CSV text into a list of shipment rows.",
        "input_schema": {
            "type": "object",
            "properties": {"csv_text": {"type": "string"}},
            "required": ["csv_text"],
            "additionalProperties": False,
        },
    },
    "flag_anomalies": {
        "fn": tools_impl.flag_anomalies,
        "description": "Return rows whose weight is missing, zero or not a number.",
        "input_schema": {
            "type": "object",
            "properties": {"rows": {"type": "array"}},
            "required": ["rows"],
            "additionalProperties": False,
        },
    },
    "fetch_vendor_status": {
        "fn": tools_impl.fetch_vendor_status,
        "description": "Call the vendor API and report whether the vendor is OK.",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"],
            "additionalProperties": False,
        },
    },
}

_PY_TYPES = {
    "string": str, "array": list, "object": dict,
    "boolean": bool, "integer": int, "number": (int, float),
}


def validate(args, schema):
    """Tiny schema check (required / unknown keys / basic types). None = valid."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required argument: {key}"
    for key, value in args.items():
        if key not in props:
            if schema.get("additionalProperties") is False:
                return f"unexpected argument: {key}"
            continue
        expected = _PY_TYPES.get(props[key].get("type"))
        if expected and (
            not isinstance(value, expected)
            or (expected is not bool and isinstance(value, bool))
        ):
            return f"argument '{key}' must be of type {props[key]['type']}"
    return None


def _auth_error():
    token = os.environ.get("MCP_TOKEN", "")
    if not token:
        # Fail closed: a service with no token configured serves nothing.
        return jsonify({"error": "server auth is not configured"}), 503
    header = request.headers.get("Authorization", "")
    supplied = header[7:] if header.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.get("/health")
def health():
    return jsonify({"ok": True, "version": VERSION, "tools": sorted(TOOLS)})


@app.get("/tools")
def list_tools():
    err = _auth_error()
    if err:
        return err
    return jsonify({
        "tools": [
            {"name": n, "description": t["description"], "input_schema": t["input_schema"]}
            for n, t in sorted(TOOLS.items())
        ]
    })


@app.post("/tools/<name>")
def invoke(name):
    err = _auth_error()
    if err:
        return err
    tool = TOOLS.get(name)
    if tool is None:
        return jsonify({"error": f"unknown tool: {name}"}), 404
    args = request.get_json(silent=True)
    if args is None:
        args = {}
    problem = validate(args, tool["input_schema"])
    if problem:
        return jsonify({"error": f"bad arguments: {problem}"}), 400
    try:
        return jsonify({"result": tool["fn"](**args)})
    except Exception:
        log.exception("tool %s crashed", name)  # details stay in the server log
        return jsonify({"error": "tool execution failed"}), 500


if __name__ == "__main__":
    # Local debugging only. In the container, gunicorn serves this app.
    app.run(host="127.0.0.1", port=8080)
