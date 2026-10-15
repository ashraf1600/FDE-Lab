"""Real crash simulation: the orchestrator process dies hard after 1 task,
then is started again. No editing of state files by hand.

Uses its own fresh RUN_ID, so it demonstrates the crash even if today's
normal run (python3 orchestrator.py) has already finished every task.
"""
import json
import os
import subprocess
import sys
import time

import storage

run_id = os.environ.get("RUN_ID") or f"crash-demo-{int(time.time())}"
env = dict(os.environ, RUN_ID=run_id, CRASH_AFTER="1")
print(f">>> run 1 of '{run_id}' (will die after the first task)")
code = subprocess.run([sys.executable, "orchestrator.py"], env=env).returncode
print(f">>> run 1 exit code: {code}")

with open(os.path.join(storage.data_dir(), "state.json")) as f:
    state = json.load(f)
print(">>> state on disk after the crash:")
print(json.dumps({k: v["status"] for k, v in state["tasks"].items()}, indent=2))

print(">>> run 2 (resume, no crash hook)")
env.pop("CRASH_AFTER")
code = subprocess.run([sys.executable, "orchestrator.py"], env=env).returncode
print(f">>> run 2 exit code: {code}")
