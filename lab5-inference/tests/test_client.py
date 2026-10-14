import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "client"))
import summarize  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    models = ["deepseek-r1:1.5b"]
    mode = "ok"          # ok | think | 500 | 404 | slow | garbage
    chat_calls = 0
    last_body = None

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": m} for m in Stub.models]})
        else:
            self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        Stub.last_body = json.loads(self.rfile.read(n))
        Stub.chat_calls += 1
        m = Stub.mode
        if m == "500":
            return self._send(500, {"error": "boom"})
        if m == "404":
            return self._send(404, {"error": "model not found"})
        if m == "slow":
            threading.Event().wait(1.0)
        if m == "garbage":
            return self._send(200, {"nope": 1})
        text = "<think>hmm\nlet me think</think>\n\nTwo sentences here." if m == "think" else "Two sentences here."
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})

    def log_message(self, *a):
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Stub.models, Stub.mode, Stub.chat_calls, Stub.last_body = ["deepseek-r1:1.5b"], "ok", 0, None
        p = mock.patch.dict(os.environ, {
            "INFERENCE_URL": f"http://127.0.0.1:{self.srv.server_port}",
            "INFERENCE_MODEL": "deepseek-r1:1.5b",
            "INFERENCE_RETRY_BACKOFF": "0",
            "INFERENCE_TIMEOUT": "5",
        })
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        tmp.write("summarize this")
        tmp.close()
        self.prompt = tmp.name
        self.addCleanup(os.unlink, tmp.name)

    def run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = summarize.main([self.prompt])
        return code, out.getvalue(), err.getvalue()

    def test_happy_path_and_request_shape(self):
        code, out, _ = self.run_main()
        self.assertEqual((code, out.strip()), (0, "Two sentences here."))
        body = Stub.last_body
        self.assertEqual(body["model"], "deepseek-r1:1.5b")
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["max_tokens"], 1024)
        self.assertEqual(body["messages"][0]["content"], "summarize this")

    def test_think_block_is_stripped(self):
        Stub.mode = "think"
        self.assertEqual(self.run_main()[1].strip(), "Two sentences here.")

    def test_think_block_kept_when_disabled(self):
        Stub.mode = "think"
        with mock.patch.dict(os.environ, {"STRIP_THINK": "0"}):
            self.assertIn("<think>", self.run_main()[1])

    def test_missing_model_gives_pull_instruction(self):
        Stub.models = ["other:1b"]
        code, _, err = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("ollama pull deepseek-r1:1.5b", err)
        self.assertEqual(Stub.chat_calls, 0)

    def test_untagged_model_matches_latest(self):
        Stub.models = ["mymodel:latest"]
        with mock.patch.dict(os.environ, {"INFERENCE_MODEL": "mymodel"}):
            self.assertEqual(self.run_main()[0], 0)

    def test_server_errors_are_retried_then_reported(self):
        Stub.mode = "500"
        code, _, err = self.run_main()
        self.assertEqual(code, 4)
        self.assertEqual(Stub.chat_calls, 3)

    def test_404_on_chat_is_model_missing_without_retry(self):
        Stub.mode = "404"
        self.assertEqual(self.run_main()[0], 3)
        self.assertEqual(Stub.chat_calls, 1)

    def test_malformed_response(self):
        Stub.mode = "garbage"
        self.assertEqual(self.run_main()[0], 4)

    def test_read_timeout_is_reported_as_unreachable(self):
        Stub.mode = "slow"
        with mock.patch.dict(os.environ, {"INFERENCE_TIMEOUT": "0.2", "INFERENCE_RETRIES": "2"}):
            self.assertEqual(self.run_main()[0], 2)
        self.assertEqual(Stub.chat_calls, 2)

    def test_server_down(self):
        with mock.patch.dict(os.environ, {"INFERENCE_URL": "http://127.0.0.1:9"}):
            code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("cannot reach", err)

    def test_unreadable_prompt_file(self):
        self.prompt = "/nonexistent/prompt.txt"
        self.assertEqual(self.run_main()[0], 4)


if __name__ == "__main__":
    unittest.main()
