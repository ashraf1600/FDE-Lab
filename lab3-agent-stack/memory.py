"""Memory layer: durable, run-scoped, per-task state.

State shape:
{
  "run_id": "2026-10-14",
  "tasks": {
    "vendor-a": {"status": "done", "attempts": 1, "result": {...},
                 "updated_at": "..."}
  }
}

status is one of: running | done | failed.
Only "done" tasks are skipped on resume. A new run_id starts clean
(the previous run is archived under memory/history/).
"""
import os
from datetime import datetime, timezone

import storage


def _state_path():
    return os.path.join(storage.data_dir(), "state.json")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(run_id):
    old = storage.read_json(_state_path(), None)
    if old and old.get("run_id") == run_id:
        return old
    if old and old.get("run_id"):
        archive = os.path.join(storage.data_dir(), "history", f"{old['run_id']}.json")
        storage.atomic_write_json(archive, old)
    return {"run_id": run_id, "tasks": {}}


def save(state):
    storage.atomic_write_json(_state_path(), state)


def get(state, task_id):
    return state["tasks"].get(task_id, {})


def record(state, task_id, status, result=None, error=None):
    """Update one task and persist immediately (one write per transition)."""
    entry = state["tasks"].setdefault(task_id, {"attempts": 0})
    if status == "running":
        entry["attempts"] += 1  # counted before executing, so a crash still counts
    entry["status"] = status
    entry["updated_at"] = _now()
    if result is not None:
        entry["result"] = result
    if error is not None:
        entry["error"] = error
    elif status == "done":
        entry.pop("error", None)
    save(state)


def summary(state):
    counts = {}
    for entry in state["tasks"].values():
        counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
    return counts
