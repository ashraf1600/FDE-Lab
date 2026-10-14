"""Tests server/entrypoint.sh against a fake `ollama` binary (no Docker, no GPU)."""
import os
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRYPOINT = os.path.join(ROOT, "server", "entrypoint.sh")

FAKE_OLLAMA = r'''#!/bin/sh
D="$FAKE_DIR"
case "$1" in
  serve)
    touch "$D/serving"
    trap 'rm -f "$D/serving"; exit 0' TERM INT
    while :; do sleep 0.1; done ;;
  list)
    [ -f "$D/serving" ] || exit 1
    echo "NAME    ID    SIZE    MODIFIED"
    [ -f "$D/models" ] && cat "$D/models"
    exit 0 ;;
  pull)
    echo "pull $2" >> "$D/calls"
    [ -n "${FAKE_PULL_FAIL:-}" ] && exit 1
    echo "$2    abc123    1.1 GB    now" >> "$D/models"
    exit 0 ;;
esac
'''


def wait_for(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        path = os.path.join(bindir, "ollama")
        with open(path, "w") as f:
            f.write(FAKE_OLLAMA)
        os.chmod(path, 0o755)
        self.env = dict(
            os.environ,
            PATH=f"{bindir}:{os.environ['PATH']}",
            FAKE_DIR=self.dir,
            OLLAMA_MODEL="deepseek-r1:1.5b",
        )

    def p(self, name):
        return os.path.join(self.dir, name)

    def start(self, **extra):
        env = dict(self.env, **extra)
        proc = subprocess.Popen(
            ["sh", ENTRYPOINT], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        def _reap():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()
        self.addCleanup(_reap)
        return proc

    def set_models(self, *lines):
        with open(self.p("models"), "w") as f:
            f.write("\n".join(lines) + "\n")

    def calls(self):
        if not os.path.exists(self.p("calls")):
            return ""
        with open(self.p("calls")) as f:
            return f.read()

    def stop(self, proc):
        proc.send_signal(signal.SIGTERM)
        return proc.wait(timeout=6)

    def test_model_already_present_no_pull_and_graceful_stop(self):
        self.set_models("deepseek-r1:1.5b    abc    1.1 GB    now")
        proc = self.start()
        self.assertTrue(wait_for(lambda: os.path.exists(self.p("serving"))))
        time.sleep(1.5)  # long enough to have passed the readiness loop
        self.assertIsNone(proc.poll())
        self.assertEqual(self.calls(), "")
        self.assertEqual(self.stop(proc), 0)
        self.assertTrue(wait_for(lambda: not os.path.exists(self.p("serving"))))

    def test_missing_model_is_pulled_then_serves(self):
        proc = self.start()
        self.assertTrue(wait_for(lambda: "pull deepseek-r1:1.5b" in self.calls()))
        time.sleep(0.5)
        self.assertIsNone(proc.poll())
        self.assertEqual(self.stop(proc), 0)

    def test_similar_tag_is_not_mistaken_for_the_model(self):
        self.set_models("deepseek-r1:1.5b-qwen-distill    abc    1 GB    now")
        proc = self.start()
        self.assertTrue(wait_for(lambda: "pull deepseek-r1:1.5b" in self.calls()))
        self.stop(proc)

    def test_failed_pull_exits_nonzero(self):
        proc = self.start(FAKE_PULL_FAIL="1")
        _, err = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("failed", err)

    def test_offline_mode_with_missing_model_fails_without_pulling(self):
        proc = self.start(PULL_ON_START="0")
        _, err = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("offline mode", err)
        self.assertEqual(self.calls(), "")

    def test_offline_mode_with_model_present_serves(self):
        self.set_models("deepseek-r1:1.5b    abc    1.1 GB    now")
        proc = self.start(PULL_ON_START="0")
        self.assertTrue(wait_for(lambda: os.path.exists(self.p("serving"))))
        time.sleep(1.5)
        self.assertIsNone(proc.poll())
        self.stop(proc)

    def test_missing_model_env_is_an_error(self):
        env = {k: v for k, v in self.env.items() if k != "OLLAMA_MODEL"}
        r = subprocess.run(["sh", ENTRYPOINT], env=env, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(r.returncode, 0)


class HealthcheckTests(unittest.TestCase):
    """The grep used by the compose healthcheck must not match similar tags."""

    CMD = 'echo "$LISTING" | grep -qE "^${OLLAMA_MODEL}[[:space:]]"'

    def check(self, listing, model="deepseek-r1:1.5b"):
        env = dict(os.environ, LISTING=listing, OLLAMA_MODEL=model)
        return subprocess.run(["sh", "-c", self.CMD], env=env).returncode

    def test_matches_exact_model(self):
        self.assertEqual(self.check("NAME ID\ndeepseek-r1:1.5b    abc    1 GB"), 0)

    def test_rejects_other_tags_and_empty_list(self):
        self.assertNotEqual(self.check("NAME ID\ndeepseek-r1:1.5b-qwen    abc    1 GB"), 0)
        self.assertNotEqual(self.check("NAME ID\ndeepseek-r1:7b    abc    5 GB"), 0)
        self.assertNotEqual(self.check("NAME ID"), 0)


if __name__ == "__main__":
    unittest.main()
