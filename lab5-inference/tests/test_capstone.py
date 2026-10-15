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


class CallTemplateTests(unittest.TestCase):
    def test_template_shows_string_placeholder(self):
        t = agent_loop.call_template("fetch_vendor_status", TOOLS["fetch_vendor_status"]["input_schema"])
        self.assertEqual(t, '{"tool": "fetch_vendor_status", "args": {"vendor_id": "<string>"}}')


class PlanTests(unittest.TestCase):
    def setUp(self):
        orig = (agent_loop.ask_model, agent_loop.ask_model_structured)
        self.addCleanup(lambda: setattr(agent_loop, "ask_model", orig[0]))
        self.addCleanup(lambda: setattr(agent_loop, "ask_model_structured", orig[1]))

    def test_tool_chosen_by_reasoning_args_by_structured_output(self):
        agent_loop.ask_model = lambda p, **k: "<think>status of a vendor -> fetch</think>fetch_vendor_status"
        seen = {}
        def structured(prompt, schema):
            seen["schema"] = schema
            return '{"tool": "fetch_vendor_status", "args": {"vendor_id": "A"}}'
        agent_loop.ask_model_structured = structured
        call, how = agent_loop.plan("Check vendor A", TOOLS)
        self.assertEqual(call, {"tool": "fetch_vendor_status", "args": {"vendor_id": "A"}})
        self.assertIn("by reasoning", how)
        # Structured output is locked to the tool the model chose.
        self.assertEqual([b["properties"]["tool"]["enum"] for b in seen["schema"]["anyOf"]],
                         [["fetch_vendor_status"]])

    def test_valid_full_call_needs_no_structuring(self):
        agent_loop.ask_model = lambda p, **k: '{"tool": "fetch_vendor_status", "args": {"vendor_id": "A"}}'
        agent_loop.ask_model_structured = lambda p, s: self.fail("should not be called")
        call, _ = agent_loop.plan("Check vendor A", TOOLS)
        self.assertEqual(call["args"], {"vendor_id": "A"})

    def test_truncated_reasoning_retries_then_exit_3(self):
        agent_loop.ask_model = lambda p, **k: "<think>still thinking about which"
        agent_loop.ask_model_structured = lambda p, s: self.fail("no tool chosen yet")
        with self.assertRaises(agent_loop.CapstoneError) as ctx:
            agent_loop.plan("Check vendor A", TOOLS)
        self.assertEqual(ctx.exception.code, agent_loop.NO_VALID_PLAN)

    def test_structured_schema_caps_strings(self):
        schema = agent_loop.tool_call_schema(TOOLS)
        args = schema["anyOf"][0]["properties"]["args"]
        self.assertEqual(args["properties"]["vendor_id"]["maxLength"], 64)
        self.assertNotIn("maxLength", TOOLS["fetch_vendor_status"]["input_schema"]["properties"]["vendor_id"])

    def test_chosen_tool_is_first_named(self):
        self.assertEqual(agent_loop.chosen_tool("fetch_vendor_status, not flag", TOOLS), "fetch_vendor_status")
        self.assertIsNone(agent_loop.chosen_tool("no idea", TOOLS))


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

    def test_wrong_type_is_rejected(self):
        # Real deepseek-r1:1.5b output seen on Poridhi.
        bad = {"tool": "fetch_vendor_status", "args": {"vendor_id": ["A"]}}
        self.assertIn("must be a JSON string", agent_loop.check_call(bad, TOOLS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
