# Lab 5: Local Inference Sidecars — Serving DeepSeek with GPU Passthrough & CPU Fallback

## Introduction

An agent loop has three moving parts: the orchestrator that decides what to do, the tools that do it, and the model that powers the reasoning between them. In Labs 3 and 4, our stack used deterministic code to simulate decisions. In this lab, we replace that hardcoded logic with a real, local Large Language Model.

External third-party LLM APIs (OpenAI, Anthropic) have real-world drawbacks: strict compliance requirements, data leakage risks in air-gapped logistics operations, unpredictable API latency, and token volume pricing. 

The production solution is a **Local Inference Sidecar**:
- One container runs an inference server (Ollama) with host GPU access or CPU fallback.
- A **Docker Named Volume** (`ollama_models:/root/.ollama`) persists model weights locally on the host disk, guaranteeing true air-gapped, zero-download restarts.
- An OpenAI-compatible HTTP interface (`/v1/chat/completions`) acts as the standard contract.
- A Python client connects to the sidecar, filters reasoning tokens (`<think>` blocks from DeepSeek-R1), and produces clean structured output.
- **Capstone Integration:** We connect L3's Orchestrator & State, L4's Containerized MCP Tool Service (with Bearer Token auth), and L5's Inference Sidecar into a complete autonomous agent stack.

![architecture](https://raw.githubusercontent.com/poridhiEng/lab-asset/refs/heads/main/Forward%20Deployed%20Engineering/Milestone%201/MDL-6%20Docker%20and%20Kubernetes%20in%20Agentic%20Workflows/Containerized%20Agent%20Runtimes/L5/architecture.png)

---

## 💻 Hardware & Computing Resource Requirements

Before starting Lab 5, verify your host machine resources. Local LLM inference places demands on RAM, Storage, and Compute:

### Resource Matrix by Model Tier:

| Component | `deepseek-r1:1.5b` (Recommended) | `deepseek-r1:7b` (Heavy) |
|---|---|---|
| **Model Weight Disk Footprint** | ~1.1 GB | ~4.7 GB |
| **Minimum System RAM** | **8 GB** | **16 GB** |
| **VRAM (If using NVIDIA GPU)** | **2 GB VRAM** | **6 - 8 GB VRAM** |
| **Compute Engine** | **CPU or GPU** (Runs smoothly on 6-core CPU) | **NVIDIA GPU Recommended** (Slow on CPU) |
| **Total Disk Space Needed (Docker + Cache)** | **~3.5 GB free** | **~8.0 GB free** |

### Host Machine Audit Check:
- **Processor:** An AMD Ryzen 5 / Intel Core i5 or higher (6 cores / 12 threads) runs `deepseek-r1:1.5b` comfortably on CPU.
- **RAM:** 8 GB RAM is the minimum threshold. With 8 GB RAM, always run `deepseek-r1:1.5b`. Running the 7B model on 8 GB RAM will cause high swap memory usage and container Out-Of-Memory (OOM) killed errors.
- **GPU:**
  - If you have an **NVIDIA GeForce / RTX** GPU with the **NVIDIA Container Toolkit** installed, you can enable GPU passthrough via `docker-compose.gpu.yml`.
  - If you have **integrated graphics (AMD Radeon iGPU / Intel Iris Xe)** or **no NVIDIA GPU**, Docker GPU passthrough cannot be used. You must use the **default CPU mode** (`docker-compose.yml`), which requires zero GPU drivers.
- **Disk Space Caution:**
  - Docker Desktop by default stores images and named volumes on drive `C:`. Ensure drive `C:` has **at least 5 GB free disk space**, or relocate your Docker / WSL virtual disk to another drive (e.g., `D:` or `E:`).

---

## The Workspace Layout

```
lab5-inference/
├── client/
│   ├── summarize.py              # Resilient client (OpenAI contract, <think> parser, retries)
│   ├── requirements.txt          # requests
│   └── Dockerfile                # non-root, read-only container
├── server/
│   ├── entrypoint.sh             # Deterministic startup, model pre-load & graceful shutdown trap
│   └── Dockerfile                # Ollama image (pinned version)
├── prompts/
│   └── vendor_incident.txt       # sample prompt payload
├── scripts/
│   └── check_gpu.sh              # verifies whether inference runs on GPU or CPU
├── tests/
│   ├── test_client.py            # Unit tests for client with mock OpenAI server
│   └── test_entrypoint.py        # Validates startup, model checking, and offline modes
├── capstone/
│   └── agent_loop.py             # THE UNIFIED AGENT: Orchestrator + MCP Tools + LLM
├── docker-compose.yml            # Default CPU composition (zero extra drivers needed)
├── docker-compose.gpu.yml        # NVIDIA GPU passthrough override
├── docker-compose.debug.yml      # Host port binding override (port 11434)
└── .env.example                  # Environment configuration
```

---

## Chapter 1 — Environment Configuration

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Configuration variables available in `.env`:
```ini
LLM_MODEL=deepseek-r1:1.5b
APP_VERSION=0.2.0
OLLAMA_VERSION=0.5.7
PULL_ON_START=1
INFERENCE_TIMEOUT=120
STRIP_THINK=1
```

---

## Chapter 2 — Package the Inference Server with Persistent Cache

In production, downloading multi-gigabyte models at runtime on every container spin-up is an anti-pattern. We package a startup entrypoint script and attach a Docker Named Volume so model weights are cached on the host.

### `server/entrypoint.sh`
Features:
- Handles both online auto-pull (`PULL_ON_START=1`) and offline/air-gapped mode (`PULL_ON_START=0`).
- Traps `SIGTERM` and `EXIT` to gracefully shut down background child processes.
- Exits non-zero immediately if model pull fails so broken states are exposed.

```bash
#!/bin/sh
set -eu

: "${OLLAMA_MODEL:?OLLAMA_MODEL must be set}"
PULL_ON_START="${PULL_ON_START:-1}"

ollama serve &
SERVER_PID=$!

PULL_PID=""
cleanup() {
  [ -n "$PULL_PID" ] && kill -TERM "$PULL_PID" 2>/dev/null || true
  kill -TERM "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT

# Wait for Ollama daemon to answer
i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "ERROR: ollama did not become ready" >&2
    exit 1
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: ollama serve exited early" >&2
    exit 1
  fi
  sleep 1
done

if ollama list | grep -qE "^${OLLAMA_MODEL}[[:space:]]"; then
  echo "Model ${OLLAMA_MODEL} already present."
elif [ "$PULL_ON_START" = "1" ]; then
  echo "Pulling ${OLLAMA_MODEL} ..."
  ollama pull "${OLLAMA_MODEL}" &
  PULL_PID=$!
  if ! wait "$PULL_PID"; then
    echo "ERROR: pulling ${OLLAMA_MODEL} failed" >&2
    exit 1
  fi
else
  echo "ERROR: model ${OLLAMA_MODEL} is not in volume and PULL_ON_START=0 (offline mode)." >&2
  exit 1
fi

wait "$SERVER_PID"
```

### `server/Dockerfile`
```dockerfile
ARG OLLAMA_VERSION=0.5.7
FROM ollama/ollama:${OLLAMA_VERSION}

ENV OLLAMA_MODEL=deepseek-r1:1.5b \
    PULL_ON_START=1 \
    OLLAMA_KEEP_ALIVE=30m

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
```

---

## Chapter 3 — Write the Resilient Client with Reasoning Parser

DeepSeek-R1 outputs internal chain-of-thought tokens inside `<think> ... </think>` tags. An automated agent loop must cleanly strip these thinking tokens so downstream systems only receive the final executive response.

### `client/summarize.py`
Features:
- Validates model availability via `/v1/models`.
- Strips `<think>` tags cleanly using regex.
- Implements exponential backoff retries for network resilience.
- Returns explicit exit codes (`0` ok, `2` unreachable, `3` model missing, `4` bad response).

---

## Chapter 4 — Compose with Model-Aware Healthcheck

In `docker-compose.yml`, the default setup runs seamlessly on **CPU**:
1. Named volume `ollama_models:/root/.ollama` for true offline/air-gapped persistence.
2. A **model-aware readiness healthcheck** that only turns healthy once DeepSeek is verified in the local model registry.
3. Client runs as a secure one-shot container (`profiles: ["job"]`, `read_only: true`, `cap_drop: [ALL]`).

### `docker-compose.yml`
```yaml
services:
  inference-server:
    image: inference-server:${APP_VERSION:-dev}
    build:
      context: ./server
      args:
        OLLAMA_VERSION: ${OLLAMA_VERSION:-0.5.7}
    environment:
      OLLAMA_MODEL: ${LLM_MODEL:-deepseek-r1:1.5b}
      PULL_ON_START: ${PULL_ON_START:-1}
    volumes:
      - ollama_models:/root/.ollama
    security_opt:
      - no-new-privileges:true
    restart: on-failure:3
    healthcheck:
      test: ["CMD-SHELL", "ollama list | grep -qE \"^$${OLLAMA_MODEL}[[:space:]]\""]
      interval: 5s
      timeout: 5s
      retries: 120
      start_period: 10s

  inference-client:
    image: inference-client:${APP_VERSION:-dev}
    build: ./client
    profiles: ["job"]
    depends_on:
      inference-server:
        condition: service_healthy
    environment:
      INFERENCE_URL: http://inference-server:11434
      INFERENCE_MODEL: ${LLM_MODEL:-deepseek-r1:1.5b}
    volumes:
      - ./prompts:/prompts:ro
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    mem_limit: 128m

volumes:
  ollama_models:
```

### For Hosts with NVIDIA GPU Passthrough (`docker-compose.gpu.yml`):
```yaml
services:
  inference-server:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
            - count: all
              capabilities: [gpu]
```

---

## Chapter 5 — How to Start Lab 5

### 1. Start the Inference Server:

**Option A — Default (CPU Mode, Works on any host):**
```bash
docker compose up -d --build --wait
```

**Option B — NVIDIA GPU Passthrough (Requires NVIDIA GPU + NVIDIA Container Toolkit):**
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build --wait
```

The `--wait` flag pauses until `inference-server` finishes pulling the model and reports `(healthy)`.

### 2. Run the Summarization Task:
```bash
docker compose run --rm inference-client
```

### 3. Verify Hardware Acceleration:
```bash
sh scripts/check_gpu.sh
```
Shows whether the model was loaded into `100% GPU` (CUDA) or `100% CPU`.

### 4. Running Unit Tests (Without Docker):
```bash
pip install requests
python -m unittest discover -s tests -v
```

---

## Chapter 6 — The Capstone: Complete Autonomous Agent Loop

Connect all three labs: **L3 (Atomic State & Queue) + L4 (Containerized MCP Tools with Bearer Auth) + L5 (Local DeepSeek Reasoning)**.

Run the capstone loop:
```bash
# Terminal 1: L4 MCP Server is running on port 8080
# Terminal 2: L5 Ollama Server is running on port 11434

python capstone/agent_loop.py
```

Execution stages:
1. **Tool Discovery:** Queries L4 MCP Tool Service (`GET /tools`) with Bearer token.
2. **LLM Decision:** DeepSeek-R1 analyzes schema, reasons through the goal, and selects `fetch_vendor_status`.
3. **Tool Execution:** Executes tool on MCP Server (`POST /tools/fetch_vendor_status`).
4. **Executive Synthesis:** Generates a clean operational summary for human operators.

---

## Chapter 7 — Teardown & Model Persistence

```bash
docker compose down
```

> **Note on Persistent Volumes:** Because we declared named volume `ollama_models:`, running `docker compose down` will NOT delete the model weights. The next time you run `docker compose up -d`, startup takes under 2 seconds with zero network downloads. To permanently remove cached weights, pass the `-v` flag (`docker compose down -v`).