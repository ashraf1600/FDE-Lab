# FDE Lab Progress & State Tracker
**Last Updated:** 2026-10-15 (review + fixes, see `lab_critical_issues.md`)

---

## 📌 Overall Status Summary

| Lab | Title | Status | Embedded Screenshots |
|---|---|---|---|
| **Lab 3** | Composing the Agent Stack: Orchestrator, Tools, Memory & Queue | **Done on Poridhi** ✅ (`lab3-agent-stack/`) | 4 / 4 retaken on Poridhi 2026-10-16 |
| **Lab 4** | Packaging MCP Servers as Containerized Services | **Done on Poridhi with Docker** ✅ | 5 / 5 taken on Poridhi 2026-10-16 |
| **Lab 5** | Local Inference Sidecars: Serving DeepSeek with GPU Passthrough | **Server, client, GPU check done on Poridhi** ✅; capstone ⏳ | 5 taken on Poridhi 2026-10-16 (needed 3 GB swap + longer timeout); unit-test shot shows 29, now 36: retake; capstone success shot pending |

---

## 📁 Lab 3 Completed Milestones & Proofs

All code tested, validated, and embedded into [L3-Composing the Agent Stack- Orchestrator-Tools- Memory and Queue.md](./L3-Composing%20the%20Agent%20Stack-%20Orchestrator-Tools-%20Memory%20and%20Queue.md):

1. **Automated Unit & Resiliency Test Suite:**
   - Asset: `assets/l3_unit_resiliency_tests.png`
   - Verified: 8/8 tests pass (`test_stack.py`), validating atomic storage, retry backoffs, exception handling, and real hard-kill recovery.
   - Highlights: Red boxes on `test_hard_kill_then_resume_does_no_duplicate_work` and `Ran 8 tests ... OK`.
2. **Queue Seeding & Idempotency Run:**
   - Asset: `assets/l3_orchestrator_run.png`
   - Verified: Tasks loaded from `queue.json`, skipped already completed tasks (`already done`).
3. **Crash Recovery & Real Process Termination Proof:**
   - Asset: `assets/l3_crash_recovery_proof.png`
   - Verified: `simulate_crash.py` hard-kills process after 1 task (`exit code: 137`); orchestrator resumes on run 2, skips `vendor-a` (`already done in run`), and completes remaining tasks with exit code 0.
   - Highlights: Red boxes on exit code 137 with disk state `{ "vendor-a": "done" }` and `Skipping vendor-a (already done in run)`.
4. **Atomic State Persistence:**
   - Asset: `assets/l3_state_json_proof.png`
   - Verified: `memory/state.json` contains scoped `run_id` ("2026-10-14") and completed (`"done"`) status for all vendors following crash recovery.
   - Highlights: Red boxes on `run_id` and `vendor-b` state.

---

## 📁 Lab 4 Completed Milestones & Proofs

All code tested, validated, and embedded into [L4-Packaging MCP Servers as Containerized Services.md](./L4-Packaging%20MCP%20Servers%20as%20Containerized%20Services.md):

1. **MCP Server Startup & Authenticated Traffic Logging:**
   - Asset: `assets/l4_mcp_server_startup.png`
   - Verified: Server started with Bearer token authentication, listening on `http://127.0.0.1:8080`, successfully responding with HTTP 200 to health and discovery requests.
   - Highlights: Red boxes on `Running on http://127.0.0.1:8080` and `GET /tools HTTP/1.1 200` logs.
2. **Tool Discovery Endpoint (`GET /tools`):**
   - Asset: `assets/l4_mcp_tools_discovery.png`
   - Verified: Clean 2-space indented JSON schema output for all 3 tools (`fetch_vendor_status`, `flag_anomalies`, `parse_manifest`) with strict validation parameters.
   - Highlights: Red boxes on Bearer token command, `fetch_vendor_status` schema, and `flag_anomalies` schema.
3. **Automated Resiliency & Unit Test Suite:**
   - Verified: 16/16 tests pass (`tests/test_tool_service.py`, `tests/test_orchestrator.py`, `tests/test_integration.py`), verifying discovery, Bearer token auth, schema validation, error masking, retries, and OS hard-kill crash recovery over live HTTP.
4. **Orchestrator Batch Run Execution:**
   - Asset: `assets/l4_orchestrator_batch_run.png`
   - Verified: `seed_queue.py` successfully queued 4 tasks; orchestrator dynamically dispatched vendor status calls and composite manifest anomaly detection to the MCP Tool Service, atomically completing all 4 tasks (`finished: {'done': 4}`).
   - Highlights: Red boxes on queue seeding, composite manifest execution, and completed run status.

---

## 🚀 Lab 5: Local Inference Sidecars & Capstone Loop

### Upgrades Applied from `lab5-fixed`:
- **Decoupled Compute:** `docker-compose.yml` defaults to CPU so it runs anywhere without requiring an NVIDIA GPU. `docker-compose.gpu.yml` is an optional override for CUDA GPUs.
- **Resilient Startup & Teardown:** `server/entrypoint.sh` includes trap handlers for clean SIGTERM shutdowns and explicit non-zero exit codes on pull failures.
- **Persistent Volume:** Model weights cached in named volume `ollama_models:/root/.ollama` (persists across `down`).
- **Model-Aware Healthcheck:** Waits specifically for `grep -qE "^$${OLLAMA_MODEL}[[:space:]]"` in `ollama list`.
- **Reasoning Cleaner:** Client strips `<think>...</think>` tokens emitted by DeepSeek-R1.
- **Capstone Integration:** `capstone/agent_loop.py` unifies L3 (Atomic State), L4 (Authenticated MCP Server), and L5 (DeepSeek-R1 Reasoning).

---

## 🛠️ Computing Resources & Host Hardware Audit

- **CPU:** AMD Ryzen 5 5500U with Radeon Graphics (6 Cores / 12 Logical Processors) — **Sufficient for `deepseek-r1:1.5b` CPU inference**.
- **RAM:** 8.0 GB RAM Total — **Enough for 1.5B model (~2GB memory footprint); 7B model requires 16GB RAM and will crash/OOM**.
- **GPU:** AMD Radeon(TM) Integrated Graphics (512 MB VRAM). **No NVIDIA discrete GPU** — **Must use CPU mode (`docker-compose.yml`)**.
- **Storage Breakdown:**
  - **Drive C:** `1.6 GB` free — ⚠️ **CRITICAL WARNING:** Docker Desktop default virtual disk location (`C:`) must be freed or relocated, as Docker image + 1.5B model requires ~3.5 GB.
  - **Drive D:** `30.4 GB` free — Ideal location for repository and caches.
  - **Drive E:** `109.4 GB` free.
- **Docker Engine:** Docker CLI / daemon is currently not in Windows PATH.
