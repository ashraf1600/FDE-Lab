import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(ROOT, "agent-stack")
SERVER = os.path.join(ROOT, "mcp-server")
for p in (AGENT, SERVER):
    if p not in sys.path:
        sys.path.insert(0, p)
