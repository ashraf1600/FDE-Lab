## Introduction

It's been three weeks since you finished L3. The four-piece agent stack — orchestrator, tools, memory, queue — is now running every night at Threadline Logistics, the logistics automation startup you've been embedded with. Asha, the head of platform, has just hired three new backend engineers to expand the team, and the cracks are starting to show.

Each of the new hires cloned the `agent-stack/` repo onto their laptop, ran `pip install requests`, and started the orchestrator locally. The tool module you wrote together — the one that talks to Threadline's vendor APIs and surfaces `fetch_vendor_status` and `parse_manifest` as tools — runs as a Python module inside the orchestrator process. Every laptop has its own copy of that module. Last week one of the new hires shipped a fix to `flag_anomalies` and forgot to push the change. Two days of nightly runs ran on stale anomaly logic before anyone noticed.

You get a Slack message from Asha.

*"Pull the tools out of the orchestrator process. Make them a service. I want one image the whole team pulls, not five laptops with five copies of the code."*

What looks like a packaging exercise is actually the moment the agent platform stops being a personal script and becomes team infrastructure. The tools become a network service. The orchestrator becomes a client. The whole thing runs in Docker so it runs the same way on Asha's laptop, the new hires' laptops, and the CI box that runs the nightly cron.

![architecture](https://raw.githubusercontent.com/poridhiEng/lab-asset/b6fb0d2cd8fb7baccef7157cfd46048792eaf249/Forward%20Deployed%20Engineering/Milestone%201/MDL-6%20Docker%20and%20Kubernetes%20in%20Agentic%20Workflows/Containerized%20Agent%20Runtimes/lab04/architecture.svg)

By the end of this lab you'll have:
- A standardized **Tool Service** that exposes tool schemas via `GET /tools` and execution via `POST /tools/<name>`,
- An Orchestrator with a **Dynamic Tool Dispatcher** that never needs to hardcode tool wrappers,
- Docker containerization for both services with volume-persisted atomic memory,
- And a production-grade `docker-compose.yml` that supports both automated one-shot nightly batch runs and interactive execution.

---

## The workspace layout

You're building on top of L3. The existing `agent-stack/` directory from the previous lab is the starting point.

```
agent-stack/                          # L3's directory, updated for production
├── memory/                           # state that survives restarts
│   ├── state.json                    # (created at runtime)
│   └── queue.json                    # (created at runtime)
├── logs/                             # orchestrator decision log
│   └── run.log                       # (created at runtime)
├── memory.py                         # atomic state manager from L3
├── job_queue.py                      # job queue from L3
├── seed_queue.py                     # seeds work
├── orchestrator.py                   # orchestrator with per-task persistence
└── tools.py                          # DYNAMIC HTTP DISPATCHER (calls MCP service)

mcp-server/                           # NEW: the Tool Service / MCP server
├── server.py                         # tool service with schemas & execution
├── tools_impl.py                     # the actual tool implementations
├── requirements.txt                  # flask + requests
└── Dockerfile                        # packages the service as an image

agent-image/                          # Dockerfile for orchestrator container
└── Dockerfile

docker-compose.yml                    # wires orchestrator + tool service
```

---

## Chapter 1 — Confirm Docker and the L3 stack

Confirm environment prerequisites:

```bash
docker --version
docker info | head -5
ls agent-stack/
```

Seed the queue to verify the L3 components are functioning:

```bash
cd agent-stack && python3 seed_queue.py
cd ..
```

---

## Chapter 2 — What a Tool Service / MCP Server Actually Is

In L3, `orchestrator.py` imported tools directly (`import tools`). In production, this causes interface drift: if someone adds a tool to the server, you shouldn't have to rewrite boilerplate in the orchestrator.

A true tool service satisfies two contracts:
1. **Discovery (`GET /tools`):** Tells the caller what tools exist, their descriptions, and their required parameter schemas (JSON Schema).
2. **Execution (`POST /tools/<name>`):** Takes arguments in JSON, runs the tool, and returns the result in JSON.

This decouples the release cycle of vendor API integrations from the orchestrator logic.

---

## Chapter 3 — Build the Tool Implementations

Move the tool implementations out of the agent stack into the dedicated `mcp-server/` directory:

```bash
mkdir -p mcp-server && cd mcp-server
```

Create `mcp-server/tools_impl.py`:

```python
import csv
import io
import requests

def parse_manifest(csv_text):
    """Tool: turn raw CSV text into a list of shipment rows."""
    reader = csv.DictReader(io.StringIO(csv_text))
    return [row for row in reader]

def flag_anomalies(rows):
    """Tool: return rows where declared weight is missing or zero."""
    flagged = []
    for row in rows:
        weight = row.get("weight", "0")
        try:
            if not weight or float(weight) == 0:
                flagged.append(row)
        except ValueError:
            flagged.append(row)
    return flagged

def fetch_vendor_status(vendor_id):
    """Tool: call an external API for a vendor's current status."""
    try:
        resp = requests.get(
            f"https://httpbin.org/get?vendor={vendor_id}",
            timeout=5,
        )
        return {"vendor": vendor_id, "ok": resp.status_code == 200}
    except Exception as e:
        return {"vendor": vendor_id, "ok": False, "error": str(e)}
```

---

## Chapter 4 — Build the Schema-Aware Tool Service

Create `mcp-server/requirements.txt`:

```
flask==3.0.3
requests==2.31.0
```

Create `mcp-server/server.py` with discovery and schema definitions:

```python
import os
from flask import Flask, request, jsonify
import tools_impl

app = Flask(__name__)

# Tool registry with JSON schemas
TOOLS = {
    "parse_manifest": {
        "fn": tools_impl.parse_manifest,
        "description": "Parse raw CSV manifest text into shipment dict rows",
        "parameters": {
            "type": "object",
            "properties": {"csv_text": {"type": "string"}},
            "required": ["csv_text"]
        }
    },
    "flag_anomalies": {
        "fn": tools_impl.flag_anomalies,
        "description": "Flag shipments with missing or zero weight",
        "parameters": {
            "type": "object",
            "properties": {"rows": {"type": "array"}},
            "required": ["rows"]
        }
    },
    "fetch_vendor_status": {
        "fn": tools_impl.fetch_vendor_status,
        "description": "Query external vendor status API",
        "parameters": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"]
        }
    }
}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "tools": list(TOOLS.keys())})

@app.route("/tools", methods=["GET"])
def list_tools():
    """Discovery endpoint: returns tool names and their JSON schemas."""
    return jsonify({
        name: {
            "description": meta["description"],
            "parameters": meta["parameters"]
        }
        for name, meta in TOOLS.items()
    })

@app.route("/tools/<name>", methods=["POST"])
def invoke(name):
    if name not in TOOLS:
        return jsonify({"error": f"unknown tool: {name}"}), 404
    
    args = request.get_json(silent=True) or {}
    try:
        result = TOOLS[name]["fn"](**args)
        return jsonify({"result": result})
    except TypeError as e:
        return jsonify({"error": f"bad arguments: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

Create `mcp-server/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tools_impl.py .
COPY server.py .

RUN useradd -m -s /bin/bash mcp
USER mcp

EXPOSE 8080
CMD ["python", "server.py"]
```

Build the image:

```bash
docker build -t threadline-mcp:latest .
cd ..
```

---

## Chapter 5 — Rewire Orchestrator as a Dynamic Client

Instead of maintaining hardcoded wrapper functions that re-introduce interface drift, the orchestrator's `tools.py` becomes a generic dispatcher.

Update `agent-stack/tools.py`:

```python
import os
import requests

MCP_URL = os.environ.get("MCP_URL", "http://mcp-server:8080")

def get_available_tools():
    """Discover available tools from the service."""
    resp = requests.get(f"{MCP_URL}/tools", timeout=5)
    resp.raise_for_status()
    return resp.json()

def call_tool(tool_name, **kwargs):
    """Generic dynamic tool invoker."""
    resp = requests.post(
        f"{MCP_URL}/tools/{tool_name}",
        json=kwargs,
        timeout=10
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]
```

Update `agent-stack/orchestrator.py` to persist after every task and handle retries properly:

```python
import os
import job_queue as q
import memory
import tools

MAX_ATTEMPTS = 3
LOG_PATH = os.path.join("logs", "run.log")

def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(msg + "\n")
    print(f"[orchestrator] {msg}")

def run():
    tasks = q.load()
    state = memory.get_or_create_run()
    log(f"Starting orchestrator run '{state['run_id']}' with {len(tasks)} tasks")

    for task in tasks:
        task_id = task["id"]
        task_state = memory.get_task_state(task_id)

        if task_state.get("status") == "done":
            log(f"Skipping {task_id} (already done)")
            continue

        if task_state.get("attempts", 0) >= MAX_ATTEMPTS:
            log(f"Skipping {task_id} (exceeded max attempts)")
            continue

        attempts = memory.record_attempt(task_id)
        log(f"Processing {task_id} via Tool Service (attempt {attempts})")

        try:
            if task["type"] == "status":
                result = tools.call_tool("fetch_vendor_status", vendor_id=task["vendor_id"])
            else:
                result = {"ok": False, "error": f"Unknown type: {task['type']}"}

            if result.get("ok"):
                memory.update_task_state(task_id, status="done", result=result)
                log(f"Completed {task_id}: {result}")
            else:
                err = result.get("error", "tool reported failure")
                memory.update_task_state(task_id, status="failed", error=err)
                log(f"Failed {task_id}: {err}")

        except Exception as e:
            memory.update_task_state(task_id, status="failed", error=str(e))
            log(f"Exception during {task_id}: {e}")

    log("Orchestrator finished run.")

if __name__ == "__main__":
    run()
```

---

## Chapter 6 — Containerize Orchestrator & Docker Compose

Create `agent-image/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests==2.31.0

COPY agent-stack/memory.py .
COPY agent-stack/job_queue.py .
COPY agent-stack/tools.py .
COPY agent-stack/orchestrator.py .

RUN mkdir -p memory logs
RUN useradd -m -s /bin/bash agent
USER agent

CMD ["python", "orchestrator.py"]
```

Build the agent image:

```bash
docker build -f agent-image/Dockerfile -t threadline-agent:latest .
```

Create `docker-compose.yml`:

```yaml
services:
  mcp-server:
    image: threadline-mcp:latest
    build: ./mcp-server
    container_name: threadline-mcp
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8080/health || exit 1"]
      interval: 3s
      timeout: 3s
      retries: 10
      start_period: 3s

  agent:
    image: threadline-agent:latest
    build:
      context: .
      dockerfile: agent-image/Dockerfile
    container_name: threadline-agent
    depends_on:
      mcp-server:
        condition: service_healthy
    environment:
      - MCP_URL=http://mcp-server:8080
    volumes:
      - ./agent-stack/memory:/app/memory
      - ./agent-stack/logs:/app/logs
```

---

## Chapter 7 — Run and Verify

Bring up the tool service:

```bash
docker compose up -d mcp-server
```

Run the nightly orchestrator job as a one-shot container:

```bash
docker compose run --rm agent
```

Check the persisted logs on the host:

```bash
cat agent-stack/logs/run.log
cat agent-stack/memory/state.json
```

Tear down services when complete:

```bash
docker compose down
```

### Production Outcomes:
1. **Zero Interface Drift:** Adding a new tool to `mcp-server` automatically advertises it on `GET /tools`.
2. **Crash-Proof Checkpointing:** State is saved atomically after each task.
3. **One-Shot Execution:** Eliminates artificial `tail -f /dev/null` workarounds in production containers.