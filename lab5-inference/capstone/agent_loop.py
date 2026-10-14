"""Capstone: Unified Agent Loop connecting L3 State/Queue, L4 MCP Tools, and L5 Local LLM.

1. Connects to L4 MCP Tool Service (with Bearer Token auth).
2. Connects to L5 Local Inference Sidecar (Ollama / DeepSeek-R1).
3. Strips DeepSeek <think> reasoning tokens to extract structured tool calls.
4. Executes the chosen tool and generates an executive summary.
"""
import json
import os
import re
import sys
import requests

INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://localhost:11434").rstrip("/")
MCP_URL = os.environ.get("MCP_URL", "http://localhost:8080").rstrip("/")
MCP_TOKEN = os.environ.get("MCP_TOKEN", "test-secret-token")
MODEL = os.environ.get("INFERENCE_MODEL", "deepseek-r1:1.5b")


def strip_think(text: str) -> str:
    """Strips DeepSeek-R1 reasoning tokens (<think>...</think>)."""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def mcp_headers():
    return {"Authorization": f"Bearer {MCP_TOKEN}"}


def consult_llm_for_action(task_desc: str, available_tools: dict) -> dict:
    """Uses local LLM to select tool and build arguments based on schema."""
    prompt = (
        f"You are an autonomous logistics agent.\n"
        f"Task: {task_desc}\n"
        f"Available Tools Schema: {json.dumps(available_tools)}\n\n"
        f"Respond ONLY with a JSON object specifying the tool to call and arguments.\n"
        f"Format: {{\"tool\": \"<tool_name>\", \"args\": {{...}}}}\n"
        f"Do not include explanation or markdown formatting."
    )

    resp = requests.post(
        f"{INFERENCE_URL}/v1/chat/completions",
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 512,
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    cleaned = strip_think(raw)

    # Extract JSON object safely
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM response: {cleaned}")
    return json.loads(cleaned[start : end + 1])


def run_capstone():
    print("=" * 60)
    print("🚀 CAPSTONE: UNIFIED AUTONOMOUS AGENT LOOP (L3 + L4 + L5)")
    print("=" * 60)

    # Step 1: Tool Discovery
    print("\n--- 1. Fetching Tool Schemas from L4 MCP Tool Service ---")
    tools_resp = requests.get(f"{MCP_URL}/tools", headers=mcp_headers(), timeout=10)
    tools_resp.raise_for_status()
    tools = tools_resp.json()
    print(f"Discovered Tools: {list(tools.keys())}")

    # Step 2: LLM Planning
    print("\n--- 2. Asking Local DeepSeek (L5) to Plan Action ---")
    task = "Check current status for vendor A"
    decision = consult_llm_for_action(task, tools)
    print(f"DeepSeek Decided: Call '{decision['tool']}' with args: {decision.get('args', {})}")

    # Step 3: Tool Execution
    print("\n--- 3. Executing Tool on Containerized MCP Service (L4) ---")
    exec_resp = requests.post(
        f"{MCP_URL}/tools/{decision['tool']}",
        headers=mcp_headers(),
        json=decision.get("args", {}),
        timeout=10,
    )
    exec_resp.raise_for_status()
    result = exec_resp.json()
    print(f"Tool Execution Result: {result}")

    # Step 4: Executive Summary
    print("\n--- 4. Generating Executive Summary via DeepSeek ---")
    summary_prompt = (
        f"Summarize this vendor status result in one professional sentence "
        f"for an operations dashboard: {result}"
    )
    resp = requests.post(
        f"{INFERENCE_URL}/v1/chat/completions",
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": summary_prompt}],
            "temperature": 0.2,
            "max_tokens": 256,
        },
        timeout=120,
    )
    resp.raise_for_status()
    summary_raw = resp.json()["choices"][0]["message"]["content"]
    summary_clean = strip_think(summary_raw)
    print(f"\nFinal Executive Report:\n{summary_clean}")
    print("\n" + "=" * 60)
    print("✅ CAPSTONE RUN COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    run_capstone()
