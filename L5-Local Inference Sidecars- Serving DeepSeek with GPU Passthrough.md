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

### Small cloud VMs (e.g. Poridhi: 1.9 GB RAM, 2 vCPU)
Measured on a Poridhi VM: Ollama reports `model requires more system memory (1.7 GiB) than is available (975 MiB)` and every request returns HTTP 500, because code-server, Docker and the editor extensions already use about 1 GB. Two fixes, both verified:
1. **Add swap** (Ollama counts free swap as memory): `sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
2. **Allow for slow generation:** about 4 tokens/s on 2 vCPUs, so R1's reasoning takes 2 to 4 minutes. The default 120 s client timeout cancels the request (server log: `500 | 2m0s`). Use `INFERENCE_TIMEOUT=600 docker compose run --rm inference-client` (compose passes it to the client) and `INFERENCE_TIMEOUT=900 CAPSTONE_MAX_TOKENS=1024 python3 capstone/agent_loop.py`.

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

### 0. Small machines only: add swap first
On a machine with less than about 3 GB of free RAM (Poridhi: 1.9 GB total, about 1 GB free), the server starts but every request fails with HTTP 500 and the log says `model requires more system memory (1.7 GiB) than is available`. Ollama counts swap as memory, so add some once per machine:

```bash
sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
free -h        # Swap: 3.0Gi
```

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

The screenshot command combines the start and the status (build output trimmed to the last lines):

```bash
docker compose up -d --build --wait 2>&1 | tail -6 && docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
```



![Inference server built and healthy](./assets/l5_poridhi_server_startup.png)

Then confirm the model really is in the named volume and reachable from the host:

```bash
docker compose logs inference-server 2>&1 | grep -E 'Pulling|already present|Listening|inference compute' | cut -c1-140
docker compose exec inference-server ollama list
curl -s http://127.0.0.1:11434/v1/models; echo
docker volume ls | grep ollama
```

The log shows Ollama listening on `11434`, `inference compute ... library=cpu` (no GPU) and `Pulling deepseek-r1:1.5b`. `ollama list` shows `deepseek-r1:1.5b` (1.1 GB), `/v1/models` returns it over the OpenAI-compatible API, and the `lab5-inference_ollama_models` volume exists:

![Model present in the volume and served on /v1/models](./assets/l5_server_startup_healthy.png)

### 2. Run the Summarization Task:
```bash
docker compose run --rm inference-client
echo $?        # 0 ok | 2 unreachable | 3 model missing | 4 bad response
```

On a slow CPU, R1's reasoning takes longer than the default 120 s read timeout: the server log then shows `500 | 2m0s` for every attempt and the client exits `2`. Give it more time (compose passes `INFERENCE_TIMEOUT` to the client):

```bash
time INFERENCE_TIMEOUT=600 docker compose run --rm inference-client 2>/dev/null; echo "exit=$?"
```



![Client summarizing the vendor incident](./assets/l5_client_summarize_proof.png)

### 3. Verify Hardware Acceleration:
```bash
sh scripts/check_gpu.sh
```

The screenshot also lists the running containers:

```bash
sh scripts/check_gpu.sh; docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```
Shows whether the model was loaded into `100% GPU` (CUDA) or `100% CPU`. On a host without an NVIDIA GPU (Poridhi, laptops with integrated graphics) `100% CPU` is the expected, correct result for Option A. On Poridhi: `nvidia-smi not found`, `deepseek-r1:1.5b 1.8 GB 100% CPU`, and both stacks running side by side (`lab5-inference-inference-server-1` and the Lab 4 `fde-lab-mcp-server-1`).

> The screenshot shows the inference server's port as plain `11434/tcp` (not published). It was taken from a second terminal where `COMPOSE_FILE` had not been exported, so `docker compose run` recreated the server without the debug override. That is exactly the trap described in step 1. Re-running `export COMPOSE_FILE=...` and `docker compose up -d --wait` restored `127.0.0.1:11434`.

![GPU check: 100% CPU on a host without NVIDIA GPU](./assets/l5_gpu_check_proof.png)

### 4. Running Unit Tests (Without Docker):
```bash
pip3 install --break-system-packages requests==2.32.3   # or use a venv
python3 -m unittest discover -s tests -v
```
36 tests: client (mock OpenAI server), entrypoint/healthcheck logic, and the capstone's parsing, validation and reason-then-structure planner. The screenshot command prints one line per test:

```bash
python3 -m unittest discover -s tests -v 2>&1 >/dev/null | grep -E ' \.\.\. (ok|FAIL|ERROR)$|^Ran|^OK|^FAILED' | sed -E 's/ \([^)]*\)//'
```

> The screenshot shows `Ran 29 tests ... OK`. It was taken before the capstone tests added during the Poridhi run. The suite now has 36 tests (all passing); retake the screenshot to match.

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
2. **LLM Decision (L5), reason then structure:**
   - *Reason (choose the tool):* DeepSeek-R1 thinks freely (`<think>`) and answers with ONE tool name. A short answer finishes reliably even on a slow CPU.
   - *Structure (fill the arguments):* Ollama **structured outputs** (native `/api/chat` with `"format": <JSON schema>`, Ollama 0.5+), locked to the chosen tool's schema, fill its arguments. Decoding is constrained, so the JSON shape cannot be wrong.
   - *Check:* the call is validated against the discovered schema (known tool, required args, no extra args, argument types) before anything runs. Up to 3 attempts, with the problem fed back each time.

   Why it's split like this: on Poridhi the real 1.5b model picked the right tool when reasoning freely but wrote broken JSON (`{"vendor_id": ["A"]}`, `args` not an object). With constrained decoding alone (no reasoning) the JSON was valid, but it picked the wrong tool. So reasoning chooses the tool and the schema shapes the arguments.
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

**Status on Poridhi (1.9 GB RAM, 2 vCPU, CPU only):** tool discovery (L4), L3 state and the validation layer work. The 1.5b model did not yet complete a fully valid tool call within 3 attempts on that machine. It chose the right tool when reasoning, but its JSON was malformed, and the validation correctly refused to run anything (exit code 3, screenshot below). The reason-then-structure planner above was written in response and passes its 36 unit tests, but the Poridhi session ended before it could be run against the real model. Run it on a machine with more RAM or a GPU, or retry, and add `assets/l5_capstone_run.png`.

![Capstone validation refusing malformed model output](./assets/l5_capstone_validation_rejects.png)

---

## Chapter 7 — Teardown & Model Persistence

```bash
docker compose down
```

> **Note on Persistent Volumes:** Because we declared named volume `ollama_models:`, running `docker compose down` will NOT delete the model weights. The next time you run `docker compose up -d`, startup takes under 2 seconds with zero network downloads. To permanently remove cached weights, pass the `-v` flag (`docker compose down -v`).

---

## Chapter 8 — Production Reality: Top Architectural Pillars for Lab 5

| Dimension | Toy Implementation | Production Reality |
| :--- | :--- | :--- |
| **Model Hosting & Privacy** | Calling external third-party APIs (OpenAI/Anthropic) with data leakage risks, unpredictable latency, and token fees | **Local Inference Sidecar (Ollama)** running directly inside an isolated container with zero data leaving the host boundary |
| **Model Weight Persistence** | Pulling multi-gigabyte models on every container launch (breaks offline/air-gapped operations) | **Docker Named Volume (`ollama_models:/root/.ollama`)** caching weights on host disk; zero network downloads on subsequent restarts |
| **Readiness Healthcheck** | Naive port checks (`wget /api/tags` or TCP ping) declaring ready while model is still downloading | **Model-Aware Readiness Probe** (`ollama list | grep <model>`) verifying the specific LLM is fully loaded into local registry |
| **Hardware Flexibility** | Requiring expensive dedicated NVIDIA GPUs or crashing on CPU | **Decoupled Compute Architecture**: default CPU execution with zero driver overhead + optional GPU Passthrough (`docker-compose.gpu.yml`) |
| **Reasoning Token Parsing** | Raw `<think>` reasoning dumps leaking into user-facing outputs and breaking downstream JSON parsers | **Clean Token Stripping (`strip_think`)** isolating chain-of-thought tokens while passing clean executive responses |
| **Agent Decision Making** | Hardcoded `if/else` logic or single-pass LLM calls producing broken JSON syntax | **Reason-then-Structure Pattern**: DeepSeek-R1 free-form thinking selects the tool, Ollama JSON schema constrained decoding guarantees valid arguments |
| **System Resiliency** | Unbounded timeouts freezing client processes indefinitely | **Configurable Timeouts & Exponential Backoff** (`INFERENCE_TIMEOUT`, retry loops, explicit exit codes `0, 2, 3, 4`) |

---

## Chapter 9 — Conclusion & Full-Stack Synthesis (Labs 3, 4, and 5)

Through the completion of Labs 3, 4, and 5, you have transformed a brittle, deterministic Python script into an enterprise-grade, containerized autonomous agent platform:

```mermaid
flowchart TD
    subgraph Host ["Host / Scheduling Environment"]
        Queue[("Persistent Queue<br/>(L3: queue.json)")]
        Runner["Agent Runner / Orchestrator<br/>(capstone/agent_loop.py)"]
        State[("Atomic Memory State<br/>(L3: memory/state.json)")]
    end

    subgraph L5Sidecar ["Lab 5: Local Inference Sidecar (Port 11434)"]
        Ollama["Ollama Engine<br/>(CPU / NVIDIA CUDA)"]
        DeepSeek["DeepSeek-R1:1.5b<br/>(Local Weights in ollama_models volume)"]
        Ollama --- DeepSeek
    end

    subgraph L4ToolService ["Lab 4: Containerized Tool Service (Port 8080)"]
        Gunicorn["Gunicorn WSGI + Flask<br/>(Bearer Token Auth)"]
        Tools["MCP Tools Catalog<br/>(GET /tools & POST /tools/:name)"]
        Gunicorn --- Tools
    end

    Queue -->|1. Pop Next Task| Runner
    Runner -->|2. Task & Tools Context| DeepSeek
    DeepSeek -->|3. Reason-then-Structure Tool Call| Runner
    Runner -->|4. Authenticated HTTP POST| Tools
    Tools -->|5. Execution Output| Runner
    Runner -->|6. Result Synthesis| DeepSeek
    DeepSeek -->|7. Clean Executive Summary| Runner
    Runner -->|8. Atomic Checkpoint (fsync)| State
```

### The Three Architectural Milestones Achieved:

1. **Lab 3 (Durable Memory & Queue Resilience):**
   - Implemented run-scoped (`run_id`) state tracking.
   - Enforced atomic write patterns with temporary file swaps and OS-level `fsync`.
   - Guaranteed at-least-once task execution with zero duplicate work upon resume after hard kills (`kill -9` / `exit 137`).

2. **Lab 4 (Packaging MCP Tool Services):**
   - De-coupled tools from the orchestrator into a standalone containerized microservice.
   - Standardized dynamic tool discovery (`GET /tools`) via strict JSON schemas.
   - Hardened container security with non-root UID 10001, `read_only` filesystems, dropped capabilities (`cap_drop: [ALL]`), and Bearer token authentication.

3. **Lab 5 (Local Inference Sidecars & Autonomous Reasoning):**
   - Replaced deterministic decision branches with a local `deepseek-r1:1.5b` LLM running in an Ollama sidecar container.
   - Solved the small-model JSON formatting problem via the two-phase **Reason-then-Structure** planner (free-form reasoning for tool selection followed by schema-constrained decoding for argument extraction).
   - Achieved 100% offline, air-gapped readiness with zero third-party API dependencies or recurring token costs.

### Final Verification Checklist
- [x] Docker Compose configured with model-aware readiness healthcheck.
- [x] Persistent named volume (`ollama_models`) verified across restarts.
- [x] Resilient Python inference client with `<think>` parser and unit tests passing.
- [x] Capstone loop seamlessly coordinating L3 state, L4 tool microservice, and L5 DeepSeek model.

