"""Real HTTP: the actual Flask app + a stub vendor API + the orchestrator as a
separate OS process (so a hard kill is a real hard kill)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from werkzeug.serving import make_server

import _paths  # noqa: F401
import server

TOKEN = "integration-token"


class Vendor(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vendor = HTTPServer(("127.0.0.1", 0), Vendor)
        cls.api = make_server("127.0.0.1", 0, server.app)
        for s in (cls.vendor, cls.api):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.vendor.shutdown()
        cls.api.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Vendor.hits = 0
        self.env = {
            "AGENT_DATA_DIR": os.path.join(self.tmp.name, "m"),
            "AGENT_LOG_DIR": os.path.join(self.tmp.name, "l"),
            "MCP_URL": f"http://127.0.0.1:{self.api.server_port}",
            "MCP_TOKEN": TOKEN,
            "VENDOR_API_URL": f"http://127.0.0.1:{self.vendor.server_port}/get",
            "RETRY_BACKOFF": "0",
            "MCP_WAIT": "2",
            "RUN_ID": "r1",
        }
        p = mock.patch.dict(os.environ, self.env)  # server thread reads these too
        p.start()
        self.addCleanup(p.stop)
        self.sh("seed_queue.py")

    def sh(self, script, **extra):
        env = dict(os.environ, **extra)
        return subprocess.run(
            [sys.executable, script], cwd=_paths.AGENT, env=env, capture_output=True
        ).returncode

    def state(self):
        with open(os.path.join(self.env["AGENT_DATA_DIR"], "state.json")) as f:
            return json.load(f)["tasks"]

    def test_full_run_through_the_real_service(self):
        self.assertEqual(self.sh("orchestrator.py"), 0)
        tasks = self.state()
        self.assertEqual({t["status"] for t in tasks.values()}, {"done"})
        self.assertEqual(tasks["manifest-1"]["result"]["rows"], 4)
        self.assertEqual(len(tasks["manifest-1"]["result"]["anomalies"]), 3)

    def test_hard_kill_then_resume_no_duplicate_vendor_calls(self):
        self.assertEqual(self.sh("orchestrator.py", CRASH_AFTER="1"), 137)
        self.assertEqual(Vendor.hits, 1)
        self.assertEqual(self.sh("orchestrator.py"), 0)
        self.assertEqual(Vendor.hits, 3)  # a before the crash, b and c after
        self.assertEqual({t["status"] for t in self.state().values()}, {"done"})

    def test_wrong_token_aborts_cleanly(self):
        self.assertEqual(self.sh("orchestrator.py", MCP_TOKEN="wrong"), 2)
        self.assertFalse(os.path.exists(os.path.join(self.env["AGENT_DATA_DIR"], "state.json")))

    def test_service_down_aborts_cleanly(self):
        self.assertEqual(self.sh("orchestrator.py", MCP_URL="http://127.0.0.1:9"), 2)
        self.assertFalse(os.path.exists(os.path.join(self.env["AGENT_DATA_DIR"], "state.json")))


if __name__ == "__main__":
    unittest.main()
