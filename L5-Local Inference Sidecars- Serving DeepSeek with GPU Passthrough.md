## Introduction

An agent loop has three moving parts: the orchestrator that decides what to do, the tools that do it, and the model that powers the reasoning between them. In L3 and L4, our stack used deterministic code to simulate decisions. In this lab, we replace that hardcoded logic with a real, local Large Language Model.

External third-party LLM APIs (OpenAI, Anthropic) have real-world drawbacks: strict compliance requirements, data leakage risks in air-gapped logistics operations, unpredictable API latency, and token volume pricing. 

The production solution is a **Local Inference Sidecar**:
- One container runs an inference server (Ollama) with host GPU access.
- A **Docker Named Volume** persists model weights locally, guaranteeing true air-gapped, zero-download restarts.
- An OpenAI-compatible HTTP interface (`/v1/chat/completions`) acts as the contract.
- A Python client connects to the sidecar, filters reasoning tokens (`<think>` blocks from DeepSeek-R1), and produces clean structured output.
- **Capstone Integration:** We connect L3's Orchestrator, L4's Tool Service, and L5's Inference Sidecar into a complete, unified autonomous agent stack.

![architecture](https://raw.githubusercontent.com/poridhiEng/lab-asset/refs/heads/main/Forward%20Deployed%20Engineering/Milestone%201/MDL-6%20Docker%20and%20Kubernetes%20in%20Agentic%20Workflows/Containerized%20Agent%20Runtimes/L5/architecture.png)

---

## The workspace layout

```
lab5-inference/
├── client/
│   ├── summarize.py              # Python client (with <think> tag parser)
│   ├── requirements.txt          # requests
│   └── Dockerfile                # packages the client
├── server/
│   ├── entrypoint.sh             # deterministic startup & model pre-load script
│   └── Dockerfile                # Ollama image + GPU passthrough config
├── prompts/
│   └── vendor_incident.txt       # sample prompt payload
├── capstone/
│   └── agent_loop.py             # THE UNIFIED AGENT: Orchestrator + Tools + LLM
└── docker-compose.yml            # wires server + client + persistent model cache
```

---

## Chapter 1 — Confirm Host GPU Passthrough

Verify that Docker has access to your host GPU via the NVIDIA Container Toolkit:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

You should see your NVIDIA driver version, CUDA version, and GPU listed. If you are on a CPU-only machine or macOS, the lab includes a CPU fallback in the Appendix.

---

## Chapter 2 — Setup Workspace & Prompts

```bash
mkdir -p lab5-inference/{client,server,prompts,capstone} && cd lab5-inference
```

Create `prompts/vendor_incident.txt`:

```text
Summarize the following vendor incident report in two sentences,
suitable for a Slack message to the head of operations.

Incident ID: INC-2026-0811
Vendor: Acme Shipping
Started: 2026-08-11 03:14 UTC
Resolved: 2026-08-11 04:02 UTC
Impact: 14 shipments delayed, no data loss.
Root cause: upstream API rate-limit hit on the 03:00 polling job.
Remediation: bump polling cadence to 10 minutes, add 429 backoff.
```

---

## Chapter 3 — Package the Inference Server with Persistent Cache

In production, downloading multi-gigabyte models at runtime on every container spin-up is an anti-pattern. We package a startup entrypoint script and attach a Docker Named Volume so model weights are cached on the host.

Create `server/entrypoint.sh`:

```bash
#!/bin/sh
set -e

# Start Ollama daemon in background
ollama serve &
SERVER_PID=$!

echo "[entrypoint] Waiting for Ollama server to bind..."
while ! wget -qO- http://127.0.0.1:11434/api/tags >/dev/null 2>&1; do
    sleep 1
done

echo "[entrypoint] Checking if model '${OLLAMA_MODEL}' is cached..."
if ! ollama list | grep -q "${OLLAMA_MODEL}"; then
    echo "[entrypoint] Pulling model '${OLLAMA_MODEL}' into persistent volume..."
    ollama pull "${OLLAMA_MODEL}"
else
    echo "[entrypoint] Model '${OLLAMA_MODEL}' already cached. Ready!"
fi

wait "$SERVER_PID"
```

Create `server/Dockerfile`:

```dockerfile
FROM ollama/ollama:0.5.7

WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV OLLAMA_MODEL=deepseek-r1:1.5b
ENV OLLAMA_KEEP_ALIVE=24h

EXPOSE 11434

ENTRYPOINT ["/entrypoint.sh"]
```

---

## Chapter 4 — Write the Client with Reasoning Parser

DeepSeek-R1 outputs internal chain-of-thought tokens inside `<think> ... </think>` tags. An automated agent loop must cleanly strip these thinking tokens so downstream systems only receive the final executive response.

Create `client/requirements.txt`:

```
requests==2.31.0
```

Create `client/summarize.py`:

```python
import os
import re
import sys
import requests

INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://inference-server:11434")
MODEL = os.environ.get("INFERENCE_MODEL", "deepseek-r1:1.5b")

def strip_think_tags(text):
    """DeepSeek-R1 emits <think>...</think> blocks. Strip them for clean output."""
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned.strip()

def chat(prompt):
    resp = requests.post(
        f"{INFERENCE_URL}/v1/chat/completions",
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw_content = resp.json()["choices"][0]["message"]["content"]
    return strip_think_tags(raw_content)

if __name__ == "__main__":
    prompt_path = sys.argv[1] if len(sys.argv) > 1 else "/prompts/vendor_incident.txt"
    with open(prompt_path, "r") as f:
        prompt = f.read()
    
    print("\n--- Model Response ---")
    print(chat(prompt))
```

Create `client/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir requests==2.31.0

COPY summarize.py .
RUN useradd -m -s /bin/bash client
USER client

CMD ["python", "summarize.py", "/prompts/vendor_incident.txt"]
```

---

## Chapter 5 — Compose with Model-Aware Healthcheck

In `docker-compose.yml`, we configure:
1. Host GPU reservation,
2. Named volume `ollama_cache` for true offline/air-gapped persistence,
3. A **model-aware readiness healthcheck** that only turns healthy once DeepSeek is verified in the local model registry.

Create `docker-compose.yml`:

```yaml
services:
  inference-server:
    image: inference-server:latest
    build: ./server
    container_name: inference-server
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    volumes:
      - ollama_cache:/root/.ollama
    healthcheck:
      # Model-aware check: passes ONLY when the model is actually ready
      test: ["CMD-SHELL", "ollama list | grep -q 'deepseek-r1:1.5b' || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 20s

  inference-client:
    image: inference-client:latest
    build: ./client
    container_name: inference-client
    depends_on:
      inference-server:
        condition: service_healthy
    environment:
      - INFERENCE_URL=http://inference-server:11434
      - INFERENCE_MODEL=deepseek-r1:1.5b
    volumes:
      - ./prompts:/prompts:ro

volumes:
  ollama_cache:
```

---

## Chapter 6 — Run the Inference Sidecar

Build and start the services:

```bash
docker compose up -d
```

Monitor container health:

```bash
docker compose ps
```

Once `inference-server` reports `(healthy)`, execute the client:

```bash
docker compose run --rm inference-client python summarize.py /prompts/vendor_incident.txt
```

---

## Chapter 7 — The Capstone: Complete Autonomous Agent Loop

Now we connect the entire stack: **L3 (Atomic State & Queue) + L4 (Containerized MCP Tools) + L5 (Local DeepSeek Reasoning)**.

Create `capstone/agent_loop.py`:

```python
import json
import os
import requests

INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://localhost:11434")
MCP_URL = os.environ.get("MCP_URL", "http://localhost:8080")
MODEL = "deepseek-r1:1.5b"

def consult_llm_for_action(task_desc, available_tools):
    """LLM acts as the decision-maker, choosing the tool."""
    prompt = f"""You are an autonomous logistics agent.
Task: {task_desc}
Available Tools: {json.dumps(available_tools)}

Respond with a JSON object specifying the tool to call and arguments.
Format: {{"tool": "<tool_name>", "args": {{...}}}}
Only output the JSON object."""
    
    resp = requests.post(
        f"{INFERENCE_URL}/v1/chat/completions",
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]},
        timeout=60
    )
    raw = resp.json()["choices"][0]["message"]["content"]
    # Extract JSON from potential markdown tags
    json_str = raw[raw.find("{"):raw.rfind("}")+1]
    return json.loads(json_str)

def run_capstone():
    print("--- 1. Fetching Tool Schemas from L4 Tool Service ---")
    tools = requests.get(f"{MCP_URL}/tools").json()
    print(f"Discovered Tools: {list(tools.keys())}")

    print("\n--- 2. Asking Local DeepSeek (L5) to Plan Action ---")
    task = "Check current status for vendor A"
    decision = consult_llm_for_action(task, tools)
    print(f"DeepSeek Decided: Call '{decision['tool']}' with {decision['args']}")

    print("\n--- 3. Executing Tool on Containerized Service (L4) ---")
    exec_resp = requests.post(f"{MCP_URL}/tools/{decision['tool']}", json=decision['args']).json()
    print(f"Tool Execution Result: {exec_resp}")

    print("\n--- 4. Generating Executive Summary via DeepSeek ---")
    summary_prompt = f"Summarize this vendor status result in one sentence: {exec_resp}"
    summary = requests.post(
        f"{INFERENCE_URL}/v1/chat/completions",
        json={"model": MODEL, "messages": [{"role": "user", "content": summary_prompt}]}
    ).json()["choices"][0]["message"]["content"]
    
    print(f"Final Agent Report: {summary.strip()}")

if __name__ == "__main__":
    # Can be run when both mcp-server and inference-server are up
    pass
```

---

## Chapter 8 — Teardown

```bash
docker compose down
```

> **Note on Persistent Volumes:** Because we declared `volumes: ollama_cache:`, running `docker compose down` will NOT delete the model weights. The next time you run `docker compose up -d`, startup will take under 2 seconds with zero network downloads.