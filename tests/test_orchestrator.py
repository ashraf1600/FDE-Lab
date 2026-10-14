import json
import os
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
import job_queue
import orchestrator
import storage
import tools

TASKS = [
    {"id": "vendor-a", "type": "status", "vendor_id": "A"},
    {"id": "vendor-b", "type": "status", "vendor_id": "B"},
    {"id": "manifest-1", "type": "manifest", "csv": "weight\n0\n"},
]


def fake_call(name, **kw):
    if name == "fetch_vendor_status":
        return {"vendor": kw["vendor_id"], "ok": True}
    if name == "parse_manifest":
        return [{"weight": "0"}]
    if name == "flag_anomalies":
        return kw["rows"]
    raise AssertionError(name)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.dict(os.environ, {
            "AGENT_DATA_DIR": os.path.join(tmp.name, "m"),
            "AGENT_LOG_DIR": os.path.join(tmp.name, "l"),
            "RETRY_BACKOFF": "0",
        })
        p.start()
        self.addCleanup(p.stop)
        job_queue.seed(TASKS)
        ready = mock.patch.object(tools, "wait_until_ready", return_value={})
        ready.start()
        self.addCleanup(ready.stop)

    def state(self):
        return storage.read_json(os.path.join(storage.data_dir(), "state.json"), None)


class Tests(Base):
    def test_resume_skips_done(self):
        with mock.patch.object(tools, "call", side_effect=fake_call) as c:
            self.assertEqual(orchestrator.run("r1"), 0)
            n = c.call_count
            self.assertEqual(orchestrator.run("r1"), 0)
            self.assertEqual(c.call_count, n)

    def test_manifest_task_uses_parse_then_flag(self):
        with mock.patch.object(tools, "call", side_effect=fake_call):
            orchestrator.run("r1")
        res = self.state()["tasks"]["manifest-1"]["result"]
        self.assertEqual(res["rows"], 1)
        self.assertEqual(res["anomalies"], [{"weight": "0"}])

    def test_vendor_failure_is_failed_not_done_then_retried(self):
        bad = lambda n, **kw: {"ok": False, "error": "down"} if n == "fetch_vendor_status" else fake_call(n, **kw)
        with mock.patch.object(tools, "call", side_effect=bad):
            self.assertEqual(orchestrator.run("r1"), 1)
        st = self.state()["tasks"]
        self.assertEqual(st["vendor-a"]["status"], "failed")
        self.assertEqual(st["manifest-1"]["status"], "done")
        with mock.patch.object(tools, "call", side_effect=fake_call):
            self.assertEqual(orchestrator.run("r1"), 0)

    def test_service_unavailable_aborts_without_touching_state(self):
        with mock.patch.object(
            tools, "wait_until_ready", side_effect=tools.ToolServiceUnavailable("down")
        ):
            self.assertEqual(orchestrator.run("r1"), 2)
        self.assertIsNone(self.state())


if __name__ == "__main__":
    unittest.main()
