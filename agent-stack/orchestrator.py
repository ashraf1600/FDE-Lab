"""Orchestrator (L4): same crash-safe loop as L3, but tools live in a service.

Exit codes: 0 all tasks done | 1 some tasks failed | 2 tool service unusable
(in which case NO task state is touched, so the next run starts cleanly).
"""
import os
import sys
import time
from datetime import datetime, timezone

import job_queue
import memory
import storage
import tools

REQUIRED_TOOLS = ("fetch_vendor_status", "parse_manifest", "flag_anomalies")


def _max_attempts():
    return int(os.environ.get("MAX_ATTEMPTS", "3"))


def _backoff():
    return float(os.environ.get("RETRY_BACKOFF", "0.5"))


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(f"[orchestrator] {line}", flush=True)
    os.makedirs(storage.log_dir(), exist_ok=True)
    with open(os.path.join(storage.log_dir(), "run.log"), "a") as f:
        f.write(line + "\n")


def execute(task):
    kind = task["type"]
    if kind == "status":
        return tools.call("fetch_vendor_status", vendor_id=task["vendor_id"])
    if kind == "manifest":
        rows = tools.call("parse_manifest", csv_text=task["csv"])
        flagged = tools.call("flag_anomalies", rows=rows)
        return {"ok": True, "rows": len(rows), "anomalies": flagged}
    raise ValueError(f"unknown task type: {kind}")


def process(state, task):
    tid = task["id"]
    attempts = _max_attempts()
    for attempt in range(1, attempts + 1):
        memory.record(state, tid, "running")
        result, error = None, None
        try:
            result = execute(task)
            if isinstance(result, dict) and result.get("ok") is True:
                memory.record(state, tid, "done", result=result)
                log(f"Completed {tid}: {result}")
                return True
            error = (result or {}).get("error", "tool reported failure")
        except Exception as e:
            error = str(e)
        memory.record(state, tid, "failed", result=result, error=error)
        log(f"Attempt {attempt}/{attempts} failed for {tid}: {error}")
        if attempt < attempts:
            time.sleep(_backoff() * 2 ** (attempt - 1))
    return False


def run(run_id=None):
    run_id = (
        run_id
        or os.environ.get("RUN_ID")
        or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    tasks = job_queue.load()

    # Preflight BEFORE touching state: a down service must not burn attempts.
    try:
        tools.wait_until_ready(
            REQUIRED_TOOLS, timeout=float(os.environ.get("MCP_WAIT", "30"))
        )
    except tools.ToolServiceError as e:
        log(f"ABORT: tool service unusable: {e}")
        return 2

    state = memory.load(run_id)
    crash_after = int(os.environ.get("CRASH_AFTER", "0"))  # test hook
    log(f"Run {run_id}: {len(tasks)} tasks, state so far {memory.summary(state)}")

    handled = 0
    for task in tasks:
        tid = task["id"]
        if memory.get(state, tid).get("status") == "done":
            log(f"Skipping {tid} (already done in run {run_id})")
            continue
        log(f"Processing {tid}")
        process(state, task)
        handled += 1
        if crash_after and handled >= crash_after:
            log(f"CRASH HOOK: dying hard after {handled} task(s)")
            os._exit(137)

    summary = memory.summary(state)
    log(f"Run {run_id} finished: {summary}")
    return 0 if summary.get("done", 0) == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(run())
