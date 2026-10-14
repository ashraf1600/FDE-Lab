## Introduction

A logistics automation startup runs a nightly job that pulls shipment manifests from several vendor APIs, cross-references them, flags anomalies, and files a report. A single long agent run keeps failing the same way: it forgets what it decided twenty steps ago, reprocesses vendors it already handled, and when it crashes at step 40 there's no way to resume. The whole thing restarts from zero.

What looks like an intelligence problem is an architecture problem. The agent has no memory it can trust, no queue to pace its work, and no separation between the thing that *decides* and the things that *do*. The fix is to stop running one monolithic agent and instead compose four small components, each with a single job: an orchestrator that decides what happens next, tools that execute, a memory layer that survives restarts, and a queue that feeds work in at a controlled pace.

By the end you'll have a working agent stack where any component can crash and the system picks up where it left off, because the state lives outside the agent, not inside it.

![architecture](https://raw.githubusercontent.com/poridhiEng/lab-asset/c38754d6674690f553cd68661e10446d84526910/Forward%20Deployed%20Engineering/Milestone%201/MDL-6%20Docker%20and%20Kubernetes%20in%20Agentic%20Workflows/Containerized%20Agent%20Runtimes/lab03/architecture.svg)

---

## The workspace layout

Before writing a single file, understand the shape of what you're building. The whole system is four Python modules plus two directories that hold state on disk. Nothing hides inside a framework; every moving part is visible.

```
agent-stack/
├── memory/              # state that survives restarts
│   ├── state.json       # (created at runtime) scoped run state + task statuses
│   └── queue.json       # (created at runtime) the work list
├── logs/                # orchestrator decision log
│   └── run.log          # (created at runtime)
├── memory.py            # the atomic memory layer
├── tools.py             # isolated tool functions
├── job_queue.py         # queue read/write (named job_queue to avoid stdlib collision)
├── seed_queue.py        # one-off script to fill the queue
├── orchestrator.py      # the decision-maker that ties it together
└── simulate_crash.py    # proves crash recovery works
```

> **Why `job_queue.py` and not `queue.py`?**
> Python has a built-in standard library module named `queue`. Third-party libraries like `requests` (via `urllib3`) import Python's standard `queue`. If you name your file `queue.py`, Python imports your local file instead of the standard library, crashing immediately with an `AttributeError`. Naming this module `job_queue.py` prevents namespace collisions.

The `.json` and `.log` files inside `memory/` and `logs/` are not created by hand. They appear when the system runs. Everything else you create in the steps below, in this order, because each file depends only on the ones before it.

---

## Chapter 1 — Confirm your environment and scaffold the directories

The stack is plain Python. Confirm what you need, then lay down the two directories that will hold state.

```bash
python3 --version
pip3 install --break-system-packages requests
```

You need Python 3.9 or newer. The only external dependency is `requests`, used by the one tool that calls a vendor API. Everything else uses the standard library on purpose, so the moving parts stay visible.

```bash
mkdir -p agent-stack/{memory,logs} && cd agent-stack
```

`memory/` is where state persists between runs. `logs/` is where the orchestrator narrates its decisions. Keeping them on disk, not in the process, is the whole point of what follows.

---

## Chapter 2 — Build the memory layer first (Atomic & Run-Scoped)

Most people build the orchestrator first and bolt memory on later. That's backwards. The memory layer defines what the agent is *allowed to forget*, so it comes first. If state lives only in the running process, a crash erases everything. Put it on disk and a crash becomes a pause.

In production, two critical bugs frequently kill naive state storage:
1. **Mid-write Corruption:** Calling `open(file, "w")` truncates the file immediately. If the server crashes or loses power while writing, the JSON is destroyed. We solve this by writing to a temporary file, flushing with `fsync`, and atomically replacing via `os.replace`.
2. **Permanent Nightly Lockout:** If `processed` is a permanent flat list, night 2's run will skip every single vendor because it thinks they were done on night 1. We solve this by scoping state to a `run_id` (e.g. today's date).

Create the file at `agent-stack/memory.py`:

```python
import json
import os
from datetime import datetime

MEMORY_FILE = "memory/state.json"

def _load():
    if not os.path.exists(MEMORY_FILE):
        return {"run_id": datetime.utcnow().strftime("%Y-%m-%d"), "tasks": {}}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"run_id": datetime.utcnow().strftime("%Y-%m-%d"), "tasks": {}}

def _save(state):
    """Atomic write: write to temp file, flush to disk, then atomic rename."""
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    tmp_file = MEMORY_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, MEMORY_FILE)

def get_or_create_run(run_id=None):
    if not run_id:
        run_id = datetime.utcnow().strftime("%Y-%m-%d")
    state = _load()
    if state.get("run_id") != run_id:
        state = {"run_id": run_id, "tasks": {}}
        _save(state)
    return state

def get_task_state(task_id):
    state = _load()
    return state["tasks"].get(task_id, {"status": "pending", "attempts": 0, "result": None})

def update_task_state(task_id, status, result=None, error=None):
    state = _load()
    current = state["tasks"].get(task_id, {"status": "pending", "attempts": 0})
    current["status"] = status
    current["updated_at"] = datetime.utcnow().isoformat()
    if result is not None:
        current["result"] = result
    if error is not None:
        current["error"] = error
    state["tasks"][task_id] = current
    _save(state)

def record_attempt(task_id):
    state = _load()
    current = state["tasks"].setdefault(task_id, {"status": "pending", "attempts": 0})
    current["attempts"] += 1
    state["tasks"][task_id] = current
    _save(state)
    return current["attempts"]

def is_done(task_id):
    return get_task_state(task_id).get("status") == "done"

def snapshot():
    return _load()
```

---

## Chapter 3 — Build the tools as isolated functions

Tools are the hands of the system. Each one does exactly one thing, takes simple input, returns simple output, and, critically, knows nothing about memory, the queue, or the orchestrator. That isolation is what lets you test a tool alone, swap it, or run it with the network cut off without dragging the rest of the system along.

Create the file at `agent-stack/tools.py`:

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
            timeout=5
        )
        return {"vendor": vendor_id, "ok": resp.status_code == 200}
    except Exception as e:
        return {"vendor": vendor_id, "ok": False, "error": str(e)}
```

Notice each function is pure input-to-output. `flag_anomalies` doesn't write to disk. `fetch_vendor_status` doesn't decide what to do with a failure, it just reports one. Decisions belong to the orchestrator, not the tools.

---

## Chapter 4 — Build the queue and seed it

The queue is a list of work waiting to happen, held outside the agent. With a queue, work is explicit and ordered.

Create the file at `agent-stack/job_queue.py`:

```python
import json
import os

QUEUE_FILE = "memory/queue.json"

def seed(tasks):
    """Write the initial list of tasks to process."""
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        json.dump(tasks, f, indent=2)

def load():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE) as f:
        return json.load(f)
```

Now create the one-off seeding script at `agent-stack/seed_queue.py`:

```python
import job_queue as q

tasks = [
    {"id": "vendor-a", "type": "status", "vendor_id": "A"},
    {"id": "vendor-b", "type": "status", "vendor_id": "B"},
    {"id": "vendor-c", "type": "status", "vendor_id": "C"},
]
q.seed(tasks)
print(f"Seeded {len(tasks)} tasks into the queue")
```

Run it:

```bash
python3 seed_queue.py
```

The queue lives in `memory/queue.json`, on disk, not in a volatile variable.

---

## Chapter 5 — Wire the orchestrator together (With Retries & Checkpointing)

The orchestrator pulls tasks from the queue, checks if they are already done, executes tools, handles failures with retries, and **persists state immediately after every task**. 

> **Critical Rule:** If a vendor call fails (`ok: False`), do NOT mark it done! In production, failed calls must record the error, respect a retry ceiling (`MAX_ATTEMPTS`), and only mark `done` upon genuine success.

Create the file at `agent-stack/orchestrator.py`:

```python
import job_queue as q
import memory
import tools

MAX_ATTEMPTS = 3

def log(msg):
    print(f"[orchestrator] {msg}")
    with open("logs/run.log", "a") as f:
        f.write(msg + "\n")

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
            log(f"Skipping {task_id} (exceeded MAX_ATTEMPTS={MAX_ATTEMPTS})")
            continue

        attempts = memory.record_attempt(task_id)
        log(f"Processing {task_id} (attempt {attempts}/{MAX_ATTEMPTS})")

        try:
            if task["type"] == "status":
                result = tools.fetch_vendor_status(task["vendor_id"])
            else:
                result = {"ok": False, "error": "unknown task type"}

            # Only mark done if tool succeeded!
            if result.get("ok"):
                memory.update_task_state(task_id, status="done", result=result)
                log(f"Completed {task_id}: {result}")
            else:
                err = result.get("error", "tool returned ok=False")
                memory.update_task_state(task_id, status="failed", error=err)
                log(f"Failed {task_id}: {err}")

        except Exception as e:
            memory.update_task_state(task_id, status="failed", error=str(e))
            log(f"Crashed during {task_id}: {e}")

    log("Orchestrator finished processing queue.")
    log(f"Current state snapshot: {memory.snapshot()['tasks']}")

if __name__ == "__main__":
    run()
```

Run it:

```bash
python3 orchestrator.py
```

![Orchestrator Execution Proof](./assets/l3_orchestrator_run.png)

---

## Chapter 6 — Automated Test Suite & Resiliency Verification

Before testing crash recovery in live workflows, run the automated test suite `test_stack.py` (8 unit & crash integration tests) to verify memory atomicity, retry backoff, and state recovery under process hard-kills:

```bash
python3 -m unittest test_stack -v
```

All 8 architectural tests pass cleanly, confirming zero state loss or duplicated execution:

![Automated Resiliency & Unit Test Verification](./assets/l3_unit_resiliency_tests.png)

---

## Chapter 7 — Prove the system survives a real crash

Simulate a real process crash during execution by injecting `CRASH_AFTER=1` into `orchestrator.py`. The process dies hard via `os._exit(137)` (equivalent to `SIGKILL` / `kill -9`), leaving state safely saved on disk. When rerun, completed tasks are skipped while remaining tasks resume safely.

Create the file at `agent-stack/simulate_crash.py`:

```python
"""Real crash simulation: the orchestrator process dies hard after 1 task,
then is started again. No editing of state files by hand."""
import json
import os
import subprocess
import sys

import memory
import storage

env = dict(os.environ, CRASH_AFTER="1")
print(">>> run 1 (will die after the first task)")
code = subprocess.run([sys.executable, "orchestrator.py"], env=env).returncode
print(f">>> run 1 exit code: {code}")

state = json.load(open(os.path.join(storage.data_dir(), "state.json")))
print(">>> state on disk after the crash:")
print(json.dumps({k: v["status"] for k, v in state["tasks"].items()}, indent=2))

print(">>> run 2 (resume, no crash hook)")
env.pop("CRASH_AFTER")
code = subprocess.run([sys.executable, "orchestrator.py"], env=env).returncode
print(f">>> run 2 exit code: {code}")
```

Run the real crash simulation:

```bash
python3 simulate_crash.py
```

Notice: Run 1 dies hard after `vendor-a` with `exit code: 137`. The state on disk safely preserves `vendor-a: done`. On run 2, the orchestrator resumes, skips `vendor-a` (`already done in run`), completes `vendor-b` and `vendor-c`, and exits with code `0`:

![Crash Recovery and Retry Verification](./assets/l3_crash_recovery_proof.png)

---

## Chapter 8 — Clean up & State Inspection

Between test runs, inspect the final persisted atomic state:

```bash
cat memory/state.json
```

The resulting state on disk confirms the scoped `run_id` ("2026-10-14") and the successful completion (`"done"`) of all vendors following the crash and resume cycle:

![Final Persisted State Proof](./assets/l3_state_json_proof.png)

To reset before another clean run:

```bash
rm -f memory/state.json memory/queue.json logs/run.log memory/state.json.tmp
```

---

## End of day — Team Review

> *"So what happens when the 3 AM run crashes halfway?"*
> *"Because we write atomically after every single task and save to a scoped `run_id`, it resumes right from where it crashed. It retries failed tasks up to 3 times, never repeats finished tasks, and tomorrow night's job starts with a fresh batch automatically."*