"""Orchestrator: the only component that talks to all the others.

Holds no state of its own. Every transition is written to disk, so a kill at
any instant can be resumed by simply running it again.

Delivery semantics: at-least-once. If the process dies after a tool ran but
before "done" was saved, that task runs again on resume. Tools should be
idempotent (fetching a status is).
"""
import os
import sys
import time
from datetime import datetime, timezone

import job_queue
import memory
import storage
import tools


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
    if task["type"] == "status":
        return tools.fetch_vendor_status(task["vendor_id"])
    raise ValueError(f"unknown task type: {task['type']}")


def process(state, task):
    """Run one task with retries. Returns True only if it really succeeded."""
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
        except Exception as e:  # tool crashed or unknown task type
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
            os._exit(137)  # no cleanup, like kill -9

    summary = memory.summary(state)
    log(f"Run {run_id} finished: {summary}")
    return 0 if summary.get("done", 0) == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(run())
