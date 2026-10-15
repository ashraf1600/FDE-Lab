"""Capstone: one agent step that uses all three labs together.

  L3  durable, run-scoped task state (agent-stack/memory.py + storage.py,
      atomic writes; a finished step is skipped when you run it again)
  L4  the MCP-style tool service: discovery (GET /tools) + calls, Bearer auth
  L5  the local DeepSeek-R1 sidecar (OpenAI-compatible /v1/chat/completions)

Flow: discover tools -> the model picks a tool + args (validated against the
JSON schema, retried with feedback if invalid) -> call the tool -> the model
writes a one-line summary -> result saved to L3 state.

Both services must be reachable from where you run this (host ports):
  repo root:        docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
  lab5-inference/:  docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d --wait

Exit codes: 0 ok | 1 the tool ran but reported failure |
            2 tool service or model server unreachable / rejected us |
            3 the model never produced a valid tool call
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# Reuse the L3 memory layer instead of re-implementing it.
sys.path.insert(0, os.path.join(REPO_ROOT, "agent-stack"))
os.environ.setdefault("AGENT_DATA_DIR", os.path.join(HERE, "memory"))
import memory  # noqa: E402

INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://localhost:11434").rstrip("/")
MCP_URL = os.environ.get("MCP_URL", "http://localhost:8080").rstrip("/")
MODEL = os.environ.get("INFERENCE_MODEL", "deepseek-r1:1.5b")
# R1 "thinks" before answering; a small budget cuts the answer off.
MAX_TOKENS = int(os.environ.get("CAPSTONE_MAX_TOKENS", "2048"))
LLM_TIMEOUT = float(os.environ.get("INFERENCE_TIMEOUT", "300"))  # CPU is slow
LLM_ATTEMPTS = int(os.environ.get("CAPSTONE_LLM_ATTEMPTS", "3"))
TASK = os.environ.get("CAPSTONE_TASK", "Check current status for vendor A")

OK, TOOL_FAILED, UNREACHABLE, NO_VALID_PLAN = 0, 1, 2, 3


class CapstoneError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _token():
    """MCP_TOKEN from the environment, else from the repo-root .env (L4)."""
    token = os.environ.get("MCP_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(os.path.join(REPO_ROOT, ".env")) as f:
            for line in f:
                if line.startswith("MCP_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    raise CapstoneError(UNREACHABLE, "MCP_TOKEN is not set (export it or put it in the repo-root .env)")


def strip_think(text):
    """Drop DeepSeek-R1 reasoning.

    Handles complete <think>...</think> blocks, a closing tag whose opening tag
    was eaten by the chat template, and an unclosed block (answer cut off by
    max_tokens), which leaves nothing usable.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def extract_tool_call(text):
    """First JSON object in the text that has a "tool" key (ignores ``` fences)."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            return obj
    return None


def check_call(call, tools):
    """None if the call matches a discovered tool's schema, else the problem."""
    name = call.get("tool")
    if name not in tools:
        return f"unknown tool {name!r}; choose one of {sorted(tools)}"
    args = call.get("args", {})
    if not isinstance(args, dict):
        return "args must be a JSON object"
    schema = tools[name]["input_schema"]
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        return f"missing required args {missing} for {name}"
    extra = [k for k in args if k not in schema.get("properties", {})]
    if extra and schema.get("additionalProperties") is False:
        return f"unexpected args {extra} for {name}"
    return None


# ---------- L4: tool service ----------

def _mcp(method, path, **kw):
    try:
        resp = requests.request(
            method, f"{MCP_URL}{path}",
            headers={"Authorization": f"Bearer {_token()}"}, timeout=15, **kw,
        )
    except requests.RequestException as e:
        raise CapstoneError(UNREACHABLE, f"tool service unreachable at {MCP_URL}: {e}") from e
    if resp.status_code in (401, 503):
        raise CapstoneError(UNREACHABLE, f"tool service rejected us (HTTP {resp.status_code}): check MCP_TOKEN")
    return resp


def discover_tools():
    resp = _mcp("GET", "/tools")
    resp.raise_for_status()
    # The service answers {"tools": [ {name, description, input_schema}, ... ]}
    return {t["name"]: t for t in resp.json()["tools"]}


def call_tool(name, args):
    resp = _mcp("POST", f"/tools/{name}", json=args)
    if resp.status_code >= 400:
        raise CapstoneError(TOOL_FAILED, f"tool call failed: HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()["result"]


# ---------- L5: local model ----------

def ask_model(prompt, max_tokens=MAX_TOKENS, temperature=0.0):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        resp = requests.post(f"{INFERENCE_URL}/v1/chat/completions", json=body, timeout=(5, LLM_TIMEOUT))
    except requests.RequestException as e:
        raise CapstoneError(UNREACHABLE, f"model server unreachable at {INFERENCE_URL}: {e}") from e
    if resp.status_code == 404:
        raise CapstoneError(UNREACHABLE, f"model '{MODEL}' not found on {INFERENCE_URL} (is it pulled?)")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def plan(task, tools):
    """Ask the model for {"tool": ..., "args": {...}}, retrying with feedback."""
    catalog = [
        {"name": n, "description": t["description"], "input_schema": t["input_schema"]}
        for n, t in sorted(tools.items())
    ]
    feedback = ""
    for attempt in range(1, LLM_ATTEMPTS + 1):
        prompt = (
            "You are an autonomous logistics agent.\n"
            f"Task: {task}\n"
            f"Available tools (JSON schema): {json.dumps(catalog)}\n\n"
            "Respond ONLY with one JSON object: "
            '{"tool": "<tool_name>", "args": {...}}\n'
            "Use exactly the argument names from input_schema. No explanation."
            f"{feedback}"
        )
        answer = strip_think(ask_model(prompt))
        call = extract_tool_call(answer)
        problem = "no JSON tool call in the answer" if call is None else check_call(call, tools)
        if problem is None:
            call.setdefault("args", {})
            return call, attempt
        print(f"    attempt {attempt}/{LLM_ATTEMPTS} rejected: {problem}")
        feedback = f"\n\nYour previous answer was invalid ({problem}). Try again."
    raise CapstoneError(NO_VALID_PLAN, f"model gave no valid tool call in {LLM_ATTEMPTS} attempts")


# ---------- the loop ----------

def run_capstone():
    run_id = os.environ.get("RUN_ID") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    task_id = "capstone:" + re.sub(r"\W+", "-", TASK.lower()).strip("-")
    state = memory.load(run_id)  # L3

    print("=" * 60)
    print("CAPSTONE: L3 state + L4 tool service + L5 local DeepSeek")
    print("=" * 60)
    print(f"run_id={run_id}  task={TASK!r}  model={MODEL}")

    if memory.get(state, task_id).get("status") == "done":
        print(f"\n[L3] '{task_id}' is already done in run {run_id}; skipping.")
        print(f"     Saved summary: {state['tasks'][task_id]['result']['summary']}")
        print("     (use a new RUN_ID to run it again)")
        return OK

    print("\n--- 1. [L4] Discover tools (GET /tools, Bearer auth) ---")
    tools = discover_tools()
    print(f"Discovered tools: {sorted(tools)}")

    memory.record(state, task_id, "running")
    try:
        print("\n--- 2. [L5] DeepSeek picks a tool ---")
        call, attempts = plan(TASK, tools)
        print(f"DeepSeek decided (attempt {attempts}): {call['tool']}({call['args']})")

        print("\n--- 3. [L4] Execute the tool (POST /tools/<name>) ---")
        result = call_tool(call["tool"], call["args"])
        print(f"Tool result: {result}")
        tool_ok = isinstance(result, dict) and result.get("ok", True) is True

        print("\n--- 4. [L5] DeepSeek writes the operator summary ---")
        summary = strip_think(ask_model(
            "Summarize this tool result in one professional sentence for an "
            f"operations dashboard. Say plainly if it failed: {json.dumps(result)}",
            temperature=0.2,
        )) or "(model returned no summary)"
        print(f"Summary: {summary}")
    except CapstoneError as e:
        memory.record(state, task_id, "failed", error=str(e))
        raise

    entry = {"tool": call["tool"], "args": call["args"], "result": result, "summary": summary}
    if tool_ok:
        memory.record(state, task_id, "done", result=entry)
    else:
        memory.record(state, task_id, "failed", result=entry, error=result.get("error", "tool reported failure"))

    print("\n--- 5. [L3] State saved atomically ---")
    print(f"{memory._state_path()}: {task_id} -> {memory.get(state, task_id)['status']}")
    print("\n" + "=" * 60)
    if tool_ok:
        print("CAPSTONE RUN COMPLETED: tool succeeded")
        print("=" * 60)
        return OK
    print("CAPSTONE RUN FINISHED, BUT THE TOOL REPORTED FAILURE (see result above)")
    print("=" * 60)
    return TOOL_FAILED


def main():
    try:
        return run_capstone()
    except CapstoneError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
