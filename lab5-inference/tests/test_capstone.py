"""Unit tests for the capstone's model-output handling (no servers needed)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "capstone"))

import agent_loop  # noqa: E402

TOOLS = {
    "fetch_vendor_status": {
        "description": "x",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"],
            "additionalProperties": False,
        },
    }
}


class StripThinkTests(unittest.TestCase):
    def test_complete_block_removed(self):
        self.assertEqual(agent_loop.strip_think("<think>a {b}</think>\nanswer"), "answer")

    def test_missing_opening_tag(self):
        self.assertEqual(agent_loop.strip_think("reasoning...</think> answer"), "answer")

    def test_unclosed_block_leaves_nothing(self):
        # Answer was cut off by max_tokens while still thinking.
        self.assertEqual(agent_loop.strip_think('<think>maybe {"tool": "x"'), "")


class ExtractToolCallTests(unittest.TestCase):
    def test_fenced_json(self):
        text = 'Sure:\n```json\n{"tool": "fetch_vendor_status", "args": {"vendor_id": "A"}}\n```'
        self.assertEqual(
            agent_loop.extract_tool_call(text),
            {"tool": "fetch_vendor_status", "args": {"vendor_id": "A"}},
        )

    def test_skips_objects_without_tool_key(self):
        text = '{"note": 1} then {"tool": "fetch_vendor_status", "args": {}}'
        self.assertEqual(agent_loop.extract_tool_call(text)["tool"], "fetch_vendor_status")

    def test_none_when_no_json(self):
        self.assertIsNone(agent_loop.extract_tool_call("I think vendor A is fine."))


class CheckCallTests(unittest.TestCase):
    def test_valid(self):
        call = {"tool": "fetch_vendor_status", "args": {"vendor_id": "A"}}
        self.assertIsNone(agent_loop.check_call(call, TOOLS))

    def test_unknown_tool(self):
        self.assertIn("unknown tool", agent_loop.check_call({"tool": "get_vendor"}, TOOLS))

    def test_missing_and_extra_args(self):
        self.assertIn("missing", agent_loop.check_call({"tool": "fetch_vendor_status", "args": {}}, TOOLS))
        bad = {"tool": "fetch_vendor_status", "args": {"vendor_id": "A", "x": 1}}
        self.assertIn("unexpected", agent_loop.check_call(bad, TOOLS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
