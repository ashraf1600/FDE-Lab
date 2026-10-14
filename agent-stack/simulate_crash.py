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
