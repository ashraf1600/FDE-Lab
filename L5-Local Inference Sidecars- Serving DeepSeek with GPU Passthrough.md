# Lab 5: Local Inference Sidecars — Serving DeepSeek with GPU Passthrough & CPU Fallback

## Introduction

An agent loop has three moving parts: the orchestrator that decides what to do, the tools that do it, and the model that powers the reasoning between them. In Labs 3 and 4, our stack used deterministic code to simulate decisions. In this lab, we replace that hardcoded logic with a real, local Large Language Model.

External third-party LLM APIs (OpenAI, Anthropic) have real-world drawbacks: strict compliance requirements, data leakage risks in air-gapped logistics operations, unpredictable API latency, and token volume pricing. 

The production solution is a **Local Inference Sidecar**:
- One container runs an inference server (Ollama) with host GPU access or CPU fallback.
- A **Docker Named Volume** (`ollama_models:/root/.ollama`) persists model weights on the host disk, so after the first pull every restart is zero-download (`PULL_ON_START=0` then runs fully offline).
- An OpenAI-compatible HTTP interface (`/v1/chat/completions`) acts as the standard contract.
- A Python client connects to the sidecar, filters reasoning tokens (`<think>` blocks from DeepSeek-R1), and produces clean structured output.
- **Capstone Integration:** One agent step that uses L3's durable run-scoped state, L4's containerized MCP-style tool service (Bearer auth), and L5's local DeepSeek to choose and call a tool.

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
│   ├── test_entrypoint.py        # Validates startup, model checking, and offline modes
│   └── test_capstone.py          # Parsing/validation of DeepSeek tool calls
├── capstone/
│   └── agent_loop.py             # Capstone: L3 state + L4 MCP tools + L5 DeepSeek
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

Configuration variables available in `.env` (all optional; these are the defaults):
```ini
LLM_MODEL=deepseek-r1:1.5b
# 1 = download the model if missing (needs internet); 0 = offline mode
PULL_ON_START=1
OLLAMA_VERSION=0.5.7
APP_VERSION=0.2.0
# Client: seconds to wait for an answer (CPU is slow), 1 = hide <think> reasoning
INFERENCE_TIMEOUT=120
STRIP_THINK=1
```

`INFERENCE_TIMEOUT` and `STRIP_THINK` are passed to the client container by `docker-compose.yml`.

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

`client/summarize.py`:

```python
"""Client for an OpenAI-compatible chat endpoint (the Ollama sidecar).

Speaks only HTTP + JSON: no Ollama SDK, no model files, no GPU knowledge.

Exit codes:  0 ok | 2 server unreachable/timeout | 3 model not on server |
             4 bad or failed response
Config (env): INFERENCE_URL, INFERENCE_MODEL, INFERENCE_TIMEOUT (read, s),
  INFERENCE_RETRIES, INFERENCE_RETRY_BACKOFF, INFERENCE_MAX_TOKENS,
  INFERENCE_TEMPERATURE, STRIP_THINK (1 = drop <think>...</think> blocks)
"""
import os
import re
import sys
import time

import requests

OK, UNREACHABLE, MODEL_MISSING, BAD_RESPONSE = 0, 2, 3, 4


class InferenceError(Exception):
    def __init__(self, exit_code, message):
        super().__init__(message)
        self.exit_code = exit_code


def _url():
    return os.environ.get("INFERENCE_URL", "http://inference-server:11434").rstrip("/")


def _model():
    return os.environ.get("INFERENCE_MODEL", "deepseek-r1:1.5b")


def _timeout():
    return (5, float(os.environ.get("INFERENCE_TIMEOUT", "120")))  # (connect, read)


def _retries():
    return int(os.environ.get("INFERENCE_RETRIES", "3"))


def _backoff():
    return float(os.environ.get("INFERENCE_RETRY_BACKOFF", "1.0"))


def strip_think(text):
    """DeepSeek-R1 may emit its reasoning inside <think>...</think>."""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def list_models():
    """Model ids the server has, or None if the backend has no /v1/models."""
    try:
        resp = requests.get(f"{_url()}/v1/models", timeout=_timeout())
    except requests.RequestException as e:
        raise InferenceError(UNREACHABLE, f"cannot reach {_url()}: {e}") from e
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise InferenceError(BAD_RESPONSE, f"/v1/models returned HTTP {resp.status_code}")
    return {m["id"] for m in resp.json().get("data", [])}


def ensure_model(model):
    available = list_models()
    if available is None:
        return  # backend cannot list models; let the chat call decide
    wanted = {model} | ({f"{model}:latest"} if ":" not in model else set())
    if not wanted & available:
        raise InferenceError(
            MODEL_MISSING,
            f"model '{model}' is not on the server (has: {sorted(available) or 'nothing'}).\n"
            f"Pull it:  docker compose exec inference-server ollama pull {model}",
        )


def chat(prompt):
    model = _model()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(os.environ.get("INFERENCE_TEMPERATURE", "0")),
        "max_tokens": int(os.environ.get("INFERENCE_MAX_TOKENS", "1024")),
        "stream": False,
    }
    attempts = _retries()
    last = None
    for i in range(attempts):
        try:
            resp = requests.post(f"{_url()}/v1/chat/completions", json=body, timeout=_timeout())
        except requests.RequestException as e:  # connection error or timeout
            last = InferenceError(UNREACHABLE, f"request failed: {e}")
        else:
            if resp.status_code == 404:
                raise InferenceError(MODEL_MISSING, f"server answered 404 for model '{model}'")
            if resp.status_code >= 500:
                last = InferenceError(BAD_RESPONSE, f"server error HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                raise InferenceError(BAD_RESPONSE, f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                try:
                    text = resp.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    raise InferenceError(BAD_RESPONSE, f"unexpected response shape: {e}") from e
                if os.environ.get("STRIP_THINK", "1") == "1":
                    text = strip_think(text)
                return text
        if i < attempts - 1:
            time.sleep(_backoff() * 2**i)
    raise last


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else "/prompts/vendor_incident.txt"
    try:
        with open(path) as f:
            prompt = f.read()
    except OSError as e:
        print(f"ERROR: cannot read prompt file: {e}", file=sys.stderr)
        return BAD_RESPONSE
    try:
        ensure_model(_model())
        print(chat(prompt))
    except InferenceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.exit_code
    return OK


if __name__ == "__main__":
    sys.exit(main())
```

`client/Dockerfile`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY summarize.py .

RUN useradd --system --uid 10001 --shell /usr/sbin/nologin client
USER client

# One-shot: run, print the summary, exit with a meaningful code.
CMD ["python", "summarize.py", "/prompts/vendor_incident.txt"]
```

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
      INFERENCE_TIMEOUT: ${INFERENCE_TIMEOUT:-120}
      STRIP_THINK: ${STRIP_THINK:-1}
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
              count: all
              capabilities: [gpu]
```

(`driver`, `count` and `capabilities` belong to **one** device entry. Splitting them into separate `-` items makes two half-defined devices and the GPU request fails.)

### Debug override (`docker-compose.debug.yml`)
The server publishes no host port by default. The capstone (Chapter 6) runs on the host, so it needs this override:
```yaml
services:
  inference-server:
    ports:
      - "127.0.0.1:11434:11434"
```

---

## Chapter 5 — How to Start Lab 5

### 1. Start the Inference Server:

Run everything from `lab5-inference/`. Set `COMPOSE_FILE` once per terminal so every later `docker compose` command uses the same files (otherwise `docker compose run` recreates the server with a different config):

**Option A — Default (CPU Mode, Works on any host, e.g. Poridhi):**
```bash
cd lab5-inference
export COMPOSE_FILE=docker-compose.yml:docker-compose.debug.yml
docker compose up -d --build --wait
docker compose ps
```

**Option B — NVIDIA GPU Passthrough (Requires NVIDIA GPU + NVIDIA Container Toolkit):**
```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.debug.yml
docker compose up -d --build --wait
```

The `--wait` flag pauses until `inference-server` finishes pulling the model and reports `(healthy)`. `docker-compose.debug.yml` only adds `127.0.0.1:11434`; it is needed for the capstone and for `curl`, not for the client container.

![Inference server built and healthy](./assets/l5_poridhi_server_startup.png)

![docker compose ps shows the server healthy](./assets/l5_server_startup_healthy.png)

### 2. Run the Summarization Task:
```bash
docker compose run --rm inference-client
echo $?        # 0 ok | 2 unreachable | 3 model missing | 4 bad response
```

![Client summarizing the vendor incident](./assets/l5_client_summarize_proof.png)

### 3. Verify Hardware Acceleration:
```bash
sh scripts/check_gpu.sh
```
Shows whether the model was loaded into `100% GPU` (CUDA) or `100% CPU`. On a host without an NVIDIA GPU (Poridhi, laptops with integrated graphics) `100% CPU` is the expected, correct result for Option A.

![GPU check: 100% CPU on a host without NVIDIA GPU](./assets/l5_gpu_check_proof.png)

### 4. Running Unit Tests (Without Docker):
```bash
pip3 install --break-system-packages requests==2.32.3   # or use a venv
python3 -m unittest discover -s tests -v
```
29 tests: client (mock OpenAI server), entrypoint/healthcheck logic, and the capstone's parsing of DeepSeek output.

![Unit tests passing](./assets/l5_unit_tests_proof.png)

---

## Chapter 6 — The Capstone: One Agent Step Across All Three Labs

The capstone (`capstone/agent_loop.py`) runs one agent step that really uses all three labs:

| Lab | What the capstone uses |
|---|---|
| L3 | `agent-stack/memory.py` + `storage.py`: the step is recorded as a run-scoped task with atomic writes, so a finished step is skipped when you run it again (state in `capstone/memory/state.json`) |
| L4 | The MCP-style tool service: `GET /tools` discovery and `POST /tools/<name>`, with the Bearer token from the repo-root `.env` |
| L5 | Local DeepSeek-R1 over `/v1/chat/completions`: it chooses the tool + arguments and writes the operator summary |

Execution stages:
1. **Tool Discovery (L4):** `GET /tools` with Bearer token.
2. **LLM Decision (L5):** DeepSeek-R1 reads the schemas and answers `{"tool": ..., "args": {...}}`. The answer is checked against the discovered schema (known tool, required args, no extra args). If the reasoning was cut off or the tool/args are invalid, the model is asked again with the error as feedback (up to 3 attempts).
3. **Tool Execution (L4):** `POST /tools/<name>`.
4. **Executive Synthesis (L5):** one-sentence summary for operators.
5. **State (L3):** saved as `done`, or `failed` if the tool reported `ok: false`.

Exit codes: `0` ok | `1` the tool ran but reported failure | `2` tool service or model server unreachable / token rejected | `3` the model never produced a valid tool call.

### Run it
Both services must be reachable from the host, so both use their debug overrides:

```bash
# Terminal 1 — repo root: L4 tool service on 127.0.0.1:8080
export COMPOSE_FILE=docker-compose.yml:docker-compose.debug.yml
docker compose up -d --wait

# Terminal 2 — lab5-inference/: L5 model on 127.0.0.1:11434 (Chapter 5, step 1)
cd lab5-inference
export COMPOSE_FILE=docker-compose.yml:docker-compose.debug.yml
docker compose up -d --wait

# Run the capstone (from lab5-inference/; reads MCP_TOKEN from ../.env)
python3 capstone/agent_loop.py
echo $?
```

On CPU each DeepSeek call takes roughly 20 seconds to 2 minutes, because R1 "thinks" before it answers. That's why the capstone allows 2048 tokens and a 300 s timeout (`CAPSTONE_MAX_TOKENS`, `INFERENCE_TIMEOUT`).

Run it a second time: the step is skipped (`already done in run ...`), because L3 state persisted it. Use `RUN_ID=demo-2 python3 capstone/agent_loop.py` to force a fresh run. Change the task with `CAPSTONE_TASK="Check current status for vendor B"`.

> If httpbin.org answers 502/503, the tool returns `ok: false`, the summary says so, the state is `failed`, and the exit code is `1`. That is the correct, honest behaviour; rerun later or set `VENDOR_API_URL` in the repo-root `.env` (see Lab 4).

![Capstone run: L3 + L4 + L5](./assets/l5_capstone_run.png)

---

## Chapter 7 — Teardown & Model Persistence

```bash
docker compose down
```

> **Note on Persistent Volumes:** Because we declared named volume `ollama_models:`, running `docker compose down` will NOT delete the model weights. The next time you run `docker compose up -d`, startup takes under 2 seconds with zero network downloads. To permanently remove cached weights, pass the `-v` flag (`docker compose down -v`).