import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import job_queue  # noqa: E402
import memory  # noqa: E402
import orchestrator  # noqa: E402
import storage  # noqa: E402
import tools  # noqa: E402

TASKS = [
    {"id": f"vendor-{c}", "type": "status", "vendor_id": c.upper()} for c in "abc"
]
OK = {"vendor": "x", "ok": True}
BAD = {"vendor": "x", "ok": False, "error": "boom"}


class Handler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(
            os.environ,
            {
                "AGENT_DATA_DIR": os.path.join(self.tmp.name, "memory"),
                "AGENT_LOG_DIR": os.path.join(self.tmp.name, "logs"),
                "RETRY_BACKOFF": "0",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        job_queue.seed(TASKS)

    def statuses(self):
        state = storage.read_json(os.path.join(storage.data_dir(), "state.json"), {})
        return {k: v["status"] for k, v in state.get("tasks", {}).items()}


class TestOrchestrator(Base):
    def test_resume_skips_done_work(self):
        with mock.patch.object(tools, "fetch_vendor_status", return_value=OK) as f:
            self.assertEqual(orchestrator.run("r1"), 0)
            self.assertEqual(f.call_count, 3)
            self.assertEqual(orchestrator.run("r1"), 0)
            self.assertEqual(f.call_count, 3)  # nothing redone

    def test_new_run_id_processes_everything_again(self):
        with mock.patch.object(tools, "fetch_vendor_status", return_value=OK) as f:
            orchestrator.run("night-1")
            orchestrator.run("night-2")
            self.assertEqual(f.call_count, 6)
        self.assertTrue(
            os.path.exists(os.path.join(storage.data_dir(), "history", "night-1.json"))
        )

    def test_failed_task_is_not_marked_done_and_is_retried_later(self):
        with mock.patch.object(tools, "fetch_vendor_status", return_value=BAD) as f:
            self.assertEqual(orchestrator.run("r1"), 1)
            self.assertEqual(f.call_count, 9)  # 3 tasks x 3 attempts
        self.assertEqual(set(self.statuses().values()), {"failed"})
        with mock.patch.object(tools, "fetch_vendor_status", return_value=OK):
            self.assertEqual(orchestrator.run("r1"), 0)
        self.assertEqual(set(self.statuses().values()), {"done"})

    def test_tool_exception_is_recorded_as_failure(self):
        with mock.patch.object(tools, "fetch_vendor_status", side_effect=RuntimeError("x")):
            self.assertEqual(orchestrator.run("r1"), 1)
        self.assertEqual(set(self.statuses().values()), {"failed"})


class TestStorage(Base):
    def test_failed_replace_keeps_old_file_intact(self):
        path = os.path.join(storage.data_dir(), "x.json")
        storage.atomic_write_json(path, {"a": 1})
        with mock.patch("os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                storage.atomic_write_json(path, {"a": 2})
        with open(path) as f:
            self.assertEqual(json.load(f), {"a": 1})

    def test_duplicate_task_ids_rejected(self):
        with self.assertRaises(ValueError):
            job_queue.seed([TASKS[0], TASKS[0]])


class TestTools(unittest.TestCase):
    def test_flag_anomalies_handles_bad_weights(self):
        rows = [
            {"weight": "5"}, {"weight": "0"}, {"weight": ""},
            {"weight": "abc"}, {},
        ]
        self.assertEqual(tools.flag_anomalies(rows), rows[1:])


class TestRealCrash(Base):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_orchestrator(self, **extra):
        env = dict(
            os.environ,
            VENDOR_API_URL=f"http://127.0.0.1:{self.server.server_port}/get",
            RUN_ID="r1",
            **extra,
        )
        return subprocess.run(
            [sys.executable, "orchestrator.py"],
            cwd=HERE, env=env, capture_output=True,
        ).returncode

    def test_hard_kill_then_resume_does_no_duplicate_work(self):
        Handler.hits = 0
        self.assertEqual(self.run_orchestrator(CRASH_AFTER="1"), 137)
        self.assertEqual(sorted(self.statuses().values()), ["done"])
        self.assertEqual(Handler.hits, 1)
        self.assertEqual(self.run_orchestrator(), 0)
        self.assertEqual(set(self.statuses().values()), {"done"})
        self.assertEqual(len(self.statuses()), 3)
        self.assertEqual(Handler.hits, 3)  # 1 before crash + 2 after, never 4+


if __name__ == "__main__":
    unittest.main(verbosity=2)
