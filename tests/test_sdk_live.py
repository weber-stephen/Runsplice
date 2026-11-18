"""Opt-in smoke checks against authenticated provider SDKs."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("AGENTDBG_LIVE_TESTS") == "1", "set AGENTDBG_LIVE_TESTS=1")
class LiveSdkTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get("OPENAI_API_KEY"), "OPENAI_API_KEY is absent")
    async def test_openai_changed_tool_result_reaches_live_model(self):
        from agentdbg.openai_sdk import OpenAISession, fork_openai

        with tempfile.TemporaryDirectory() as root:
            session = OpenAISession("examples.openai_live:make_agent", root, True,
                                    read_only_tools=("lookup_order",))
            await session.run("What is the status of order A1? Call lookup_order once, then answer.")
            step = next(item["sequence"] for item in session.store.get_steps(session.run_id)
                        if item["operation"] == "tool" and item["request"]["name"] == "lookup_order")
            fork = await fork_openai(session.store, session.run_id, step,
                                     "Order A1 is delivered.", True)
            result = session.store.get_run(fork)
            self.assertTrue(result["override_applied"])
            self.assertEqual(result["status"], "live_complete")
            self.assertGreater(result["live_calls"], 0)
            self.assertIn("delivered", session.store.checkpoint(fork,
                len(session.store.get_steps(fork)))["state"]["answer"].lower())

    async def test_claude_changed_read_result_reaches_live_model(self):
        from agentdbg.claude_sdk import ClaudeSession, fork_claude

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"AGENTDBG_TEST_CWD": root}):
            document = Path(root) / "invoice.txt"
            document.write_text("invoice status: unpaid\n")
            session = ClaudeSession("tests.sdk_factories:claude_live_options", Path(root) / "store", True)
            await session.run(f"Use Read on {document} and report the invoice status in one word.")
            selected = next(item for item in session.store.get_steps(session.run_id)
                            if item["operation"] == "tool" and item["request"]["name"] == "Read")
            original = json.dumps(selected["response"])
            self.assertIn("unpaid", original)
            replacement = json.loads(original.replace("unpaid", "paid"))
            fork = await fork_claude(session.store, session.run_id, selected["sequence"], replacement)
            result = session.store.get_run(fork)
            self.assertTrue(result["override_applied"])
            self.assertEqual(result["status"], "live_complete")
            self.assertIn("paid", session.store.checkpoint(fork,
                len(session.store.get_steps(fork)))["state"]["answer"].lower())
