import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch


try:
    from agentdbg.openai_sdk import OpenAISession, fork_openai
except ImportError:
    OpenAISession = None

try:
    from agentdbg.claude_sdk import ClaudeSession, fork_claude
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolResultBlock, UserMessage
    from claude_agent_sdk.types import TextBlock
except ImportError:
    ClaudeSession = None


@unittest.skipUnless(OpenAISession, "OpenAI SDK extra unavailable")
class OpenAISessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_turn_capture_and_live_tool_result_fork(self):
        with tempfile.TemporaryDirectory() as root:
            original = OpenAISession("tests.sdk_factories:openai_agent", root, True,
                                     read_only_tools=("invoice_status",))
            self.assertEqual((await original.run("Is invoice paid?")).final_output, "unpaid")
            self.assertEqual((await original.run("Check again")).final_output, "follow-up")
            source = original.store.get_steps(original.run_id)
            self.assertEqual(original.store.session(original.run_id)["turns"],
                             ["Is invoice paid?", "Check again"])
            offline = await fork_openai(original.store, original.run_id, 2, "paid")
            self.assertEqual(original.store.get_run(offline)["status"], "blocked")
            fork = await fork_openai(original.store, original.run_id, 2, "paid", True)
            self.assertEqual(original.store.get_run(fork)["status"], "live_complete")
            self.assertTrue(original.store.get_run(fork)["override_applied"])
            self.assertEqual(original.store.checkpoint(fork, 3)["state"]["answer"], "paid")
            self.assertEqual(original.store.get_steps(original.run_id), source)
            from agentdbg.ui import compare_view, run_view
            self.assertTrue(run_view(original.store, original.run_id)["steps"][1]["can_fork"])
            self.assertFalse(run_view(original.store, original.run_id)["steps"][1]["can_replay"])
            self.assertEqual(compare_view(original.store, original.run_id, fork)["answer_comparison"],
                             {"original": "follow-up", "fork": "follow-up"})

    async def test_model_response_can_be_replaced_without_live_calls(self):
        with tempfile.TemporaryDirectory() as root:
            original = OpenAISession("tests.sdk_factories:openai_agent", root, True,
                                     read_only_tools=("invoice_status",))
            await original.run("Is invoice paid?")
            replacement = original.store.get_steps(original.run_id)[2]["response"]
            replacement["output"][0]["content"][0]["text"] = "paid"
            fork = await fork_openai(original.store, original.run_id, 3, replacement)
            self.assertEqual(original.store.get_run(fork)["status"], "replayed")
            self.assertEqual(original.store.get_run(fork)["live_calls"], 0)
            self.assertEqual(original.store.checkpoint(fork, 3)["state"]["answer"], "paid")

    async def test_metadata_only_cannot_fork(self):
        with tempfile.TemporaryDirectory() as root:
            original = OpenAISession("tests.sdk_factories:openai_agent", root)
            await original.run("Is invoice paid?")
            with self.assertRaisesRegex(ValueError, "cannot be forked"):
                await fork_openai(original.store, original.run_id, 2, "paid", True)

    async def test_changed_model_or_tool_schema_blocks_before_override(self):
        from agents.testing import ScriptedModel
        from tests.sdk_factories import openai_agent

        with tempfile.TemporaryDirectory() as root:
            original = OpenAISession("tests.sdk_factories:openai_agent", root, True)
            await original.run("Is invoice paid?")

            class OtherModel(ScriptedModel):
                pass

            def changed_model():
                return openai_agent().clone(model=OtherModel())

            def changed_schema():
                agent = openai_agent()
                tool = replace(agent.tools[0], params_json_schema={"type": "object", "properties": {
                    "invoice_id": {"type": "string"}}})
                return agent.clone(tools=[tool])

            for factory in (changed_model, changed_schema):
                with self.subTest(factory=factory.__name__), patch(
                    "agentdbg.openai_sdk.load_factory", return_value=factory):
                    fork = await fork_openai(original.store, original.run_id, 2, "paid", True)
                    self.assertEqual(original.store.get_run(fork)["status"], "blocked")
                    self.assertFalse(original.store.get_run(fork)["override_applied"])
                    self.assertEqual(original.store.get_run(fork)["live_calls"], 0)

    async def test_unapproved_live_tool_never_invokes_its_body(self):
        from agentdbg.openai_sdk import _OpenAIFork, ReplayStopped

        with tempfile.TemporaryDirectory() as root:
            original = OpenAISession("tests.sdk_factories:openai_agent", root, True)
            await original.run("Is invoice paid?")
            fork = _OpenAIFork(original.store, original.run_id, 2, "paid", True)
            fork.applied = 1
            fork.cursor = 2
            called = False

            async def invoke():
                nonlocal called
                called = True
                return "side effect"

            with self.assertRaises(ReplayStopped):
                await fork.call("tool", {"name": "invoice_status", "arguments": {}}, invoke)
            self.assertFalse(called)
            self.assertEqual(fork.store.get_steps(fork.run_id)[0]["status"], "blocked")

    async def test_redacted_request_is_viewable_but_not_forkable(self):
        with tempfile.TemporaryDirectory() as root:
            original = OpenAISession("tests.sdk_factories:openai_agent", root, True,
                                     redact_fields=("input",))
            await original.run("Is invoice paid?")
            self.assertFalse(original.store.get_run(original.run_id)["replayable"])
            with self.assertRaisesRegex(ValueError, "cannot be forked"):
                await fork_openai(original.store, original.run_id, 2, "paid")


@unittest.skipUnless(ClaudeSession, "Claude SDK extra unavailable")
class ClaudeSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_fork_tool_set_intersects_factory_permissions(self):
        from agentdbg.claude_sdk import _permitted_read_only_tools
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(tools=["Read", "WebSearch", "Bash"],
            allowed_tools=["Read", "WebSearch", "Bash"], disallowed_tools=["WebSearch"])
        self.assertEqual(_permitted_read_only_tools(options), ["Read"])

    async def test_multi_turn_capture_and_tool_override_reaches_model(self):
        seen = []

        async def fake_query(prompt, options):
            pre = options.hooks.get("PreToolUse", [])
            if pre:
                self.assertEqual(options.tools, ["Read"])
                self.assertEqual(options.allowed_tools, ["Read"])
                decision = await pre[0].hooks[0]({"tool_name": "Read", "tool_input": {"file_path": "invoice.txt"}}, "id", {})
                self.assertEqual(decision, {})
            hook = options.hooks["PostToolUse"][0].hooks[0]
            update = await hook({"tool_name": "Read", "tool_input": {"file_path": "invoice.txt"},
                                 "tool_response": "unpaid", "tool_use_id": "call-1"}, "id", {})
            value = update.get("hookSpecificOutput", {}).get("updatedToolOutput", "unpaid")
            seen.append(value)
            yield UserMessage(content=[ToolResultBlock(tool_use_id="call-1", content=value)])
            yield AssistantMessage(content=[TextBlock(text=str(value))], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id=f"session-{len(seen)}", result=str(value))

        messages = lambda *args, **kwargs: [
            *(types.SimpleNamespace(uuid=f"message-{i}", type="assistant") for i in range(1, len(seen) + 1)),
            types.SimpleNamespace(uuid=f"trailing-{len(seen)}", type="user")]
        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages), patch(
            "agentdbg.claude_sdk.fork_session", return_value=types.SimpleNamespace(session_id="branch")):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            await original.run("Check invoice")
            await original.run("Check again")
            source = original.store.get_steps(original.run_id)
            self.assertEqual(seen, ["unpaid", "unpaid"])
            self.assertEqual(original.store.session(original.run_id)["turn_end_ids"],
                             ["message-1", "message-2"])
            model_requests = [item["request"] for item in source if item["operation"] == "model"]
            self.assertEqual(model_requests[0]["submitted_prompt"], "Check invoice")
            self.assertEqual(model_requests[1]["prior_prompts"], ["Check invoice"])
            self.assertIn(3, model_requests[1]["observed_step_sequences"])
            fork = await fork_claude(original.store, original.run_id, 3, "paid")
            self.assertEqual(seen[2], "paid")
            self.assertEqual(original.store.get_run(fork)["status"], "live_complete")
            self.assertTrue(original.store.get_run(fork)["override_applied"])
            self.assertEqual(original.store.get_steps(original.run_id), source)
            from agentdbg.ui import compare_view, run_view
            view = run_view(original.store, original.run_id)
            self.assertTrue(view["steps"][0]["can_fork"])
            self.assertFalse(view["steps"][1]["can_fork"])
            self.assertEqual(compare_view(original.store, original.run_id, fork)["answer_comparison"],
                             {"original": "unpaid", "fork": "paid"})

    async def test_unsafe_tool_denied_before_execution(self):
        async def fake_query(prompt, options):
            guard = options.hooks["PreToolUse"][0].hooks[0]
            result = await guard({"tool_name": "Bash", "tool_input": {"command": "touch x"}}, "id", {})
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="fork", result="blocked")

        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query):
            from agentdbg.store import RunStore
            store = RunStore(Path(root))
            parent = store.create_run("claude", True, provider="claude")
            store.append_step(parent, {"operation": "tool", "request": {"name": "Read", "arguments": {}, "turn": 1},
                                       "response": "old"})
            store.set_session(parent, {"factory": "tests.sdk_factories:claude_options", "turns": ["x"],
                                       "turn_end_ids": ["m"], "sdk_session_id": "s", "cwd": None})
            fork = await fork_claude(store, parent, 1, "new")
            self.assertEqual(store.get_run(fork)["status"], "blocked")

    async def test_missing_matching_call_reports_override_not_applied(self):
        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            await hook({"tool_name": "Read", "tool_input": {"file_path": "different"},
                        "tool_response": "old"}, "id", {})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="fork", result="old")

        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query):
            from agentdbg.store import RunStore
            store = RunStore(Path(root))
            parent = store.create_run("claude", True, provider="claude")
            store.append_step(parent, {"operation": "tool", "request": {"name": "Read", "arguments": {"file_path": "original"}, "turn": 1},
                                       "response": "old"})
            store.set_session(parent, {"factory": "tests.sdk_factories:claude_options", "turns": ["x"],
                                       "turn_end_ids": ["m"], "sdk_session_id": "s", "cwd": None})
            fork = await fork_claude(store, parent, 1, "new")
            self.assertEqual(store.get_run(fork)["status"], "override_not_applied")
            self.assertFalse(store.get_run(fork)["override_applied"])

    async def test_rejected_nested_tool_output_is_not_reported_as_applied(self):
        from agentdbg.store import RunStore

        original_output = {"file": {"content": "unpaid"}, "status": "ok"}
        replacement = {"file": ["paid"], "status": "ok"}
        observed_output = original_output

        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            proposal = await hook({"tool_name": "Read", "tool_input": {"file_path": "invoice.txt"},
                                   "tool_response": original_output, "tool_use_id": "call-1"}, "id", {})
            self.assertEqual(proposal["hookSpecificOutput"]["updatedToolOutput"], replacement)
            answer = observed_output["file"]["content"]
            yield UserMessage(content=[ToolResultBlock(tool_use_id="call-1", content=answer)],
                              tool_use_result=observed_output)
            yield AssistantMessage(content=[TextBlock(text=answer)], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="fork", result=answer)

        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query):
            store = RunStore(Path(root))
            parent = store.create_run("claude", True, provider="claude")
            store.append_step(parent, {"operation": "tool", "request": {"name": "Read",
                "arguments": {"file_path": "invoice.txt"}, "turn": 1}, "response": original_output})
            store.set_session(parent, {"factory": "tests.sdk_factories:claude_options", "turns": ["read invoice"],
                                       "turn_end_ids": ["message-1"], "sdk_session_id": "session", "cwd": None})
            fork = await fork_claude(store, parent, 1, replacement)
            self.assertEqual(store.get_run(fork)["status"], "override_not_applied")
            self.assertFalse(store.get_run(fork)["override_applied"])
            self.assertEqual(store.get_steps(fork)[0]["origin"], "live")
            self.assertEqual(store.get_steps(fork)[0]["response"], original_output)
            self.assertEqual(store.checkpoint(fork, 2)["state"]["answer"], "unpaid")
            replacement = {"file": {"content": "paid"}, "status": "ok"}
            observed_output = replacement
            accepted = await fork_claude(store, parent, 1, replacement)
            self.assertEqual(store.get_run(accepted)["status"], "live_complete")
            self.assertTrue(store.get_run(accepted)["override_applied"])
            self.assertEqual(store.get_steps(accepted)[0]["response"], replacement)

    async def test_unconfirmed_tool_output_is_not_reported_as_applied(self):
        from agentdbg.store import RunStore

        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            await hook({"tool_name": "Read", "tool_input": {"file_path": "invoice.txt"},
                        "tool_response": "unpaid", "tool_use_id": "call-1"}, "id", {})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="fork", result="unpaid")

        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query):
            store = RunStore(Path(root))
            parent = store.create_run("claude", True, provider="claude")
            store.append_step(parent, {"operation": "tool", "request": {"name": "Read",
                "arguments": {"file_path": "invoice.txt"}, "turn": 1}, "response": "unpaid"})
            store.set_session(parent, {"factory": "tests.sdk_factories:claude_options", "turns": ["read invoice"],
                                       "turn_end_ids": ["message-1"], "sdk_session_id": "session", "cwd": None})
            fork = await fork_claude(store, parent, 1, "paid")
            self.assertEqual(store.get_run(fork)["status"], "override_not_applied")
            self.assertFalse(store.get_run(fork)["override_applied"])
            self.assertEqual(store.get_steps(fork)[0]["status"], "unconfirmed")

    async def test_later_turn_branches_from_previous_completed_message(self):
        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            update = await hook({"tool_name": "Read", "tool_input": {"file_path": "x"},
                                 "tool_response": "old", "tool_use_id": "call-1"}, "id", {})
            answer = update.get("hookSpecificOutput", {}).get("updatedToolOutput", "old")
            yield UserMessage(content=[ToolResultBlock(tool_use_id="call-1", content=answer)])
            yield AssistantMessage(content=[TextBlock(text=answer)], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result=answer)

        count = 0
        def messages(*args, **kwargs):
            nonlocal count
            count += 1
            return [types.SimpleNamespace(uuid=f"message-{i}", type="assistant")
                    for i in range(1, count + 1)]

        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages), patch(
            "agentdbg.claude_sdk.fork_session", return_value=types.SimpleNamespace(session_id="branch")) as native_fork:
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            await original.run("first")
            await original.run("second")
            fork = await fork_claude(original.store, original.run_id, 3, "new")
            self.assertEqual(native_fork.call_args.kwargs["up_to_message_id"], "message-1")
            self.assertEqual(original.store.get_run(fork)["status"], "live_complete")
            self.assertEqual(original.store.checkpoint(fork, 4)["state"]["answer"], "new")

    async def test_repeated_matching_tool_call_changes_selected_occurrence(self):
        seen = []

        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            values = []
            for index in range(2):
                update = await hook({"tool_name": "Read", "tool_input": {"file_path": "x"},
                                     "tool_response": "old", "tool_use_id": f"call-{index}"}, "id", {})
                value = update.get("hookSpecificOutput", {}).get("updatedToolOutput", "old")
                seen.append(value)
                values.append(value)
            for index, value in enumerate(values):
                yield UserMessage(content=[ToolResultBlock(tool_use_id=f"call-{index}", content=value)])
            yield AssistantMessage(content=[TextBlock(text=seen[-1])], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result=seen[-1])

        messages = lambda *args, **kwargs: [types.SimpleNamespace(uuid="assistant-1", type="assistant")]
        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            await original.run("read twice")
            fork = await fork_claude(original.store, original.run_id, 2, "new")
            self.assertEqual(seen, ["old", "old", "old", "new"])
            self.assertEqual(original.store.get_run(fork)["status"], "live_complete")
            first = await fork_claude(original.store, original.run_id, 1, "new")
            self.assertEqual([item["origin"] for item in original.store.get_steps(first)[:2]],
                             ["overridden", "live"])

    async def test_missing_assistant_anchor_marks_capture_error(self):
        async def fake_query(prompt, options):
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result="ok")

        messages = lambda *args, **kwargs: [types.SimpleNamespace(uuid="user-1", type="user")]
        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            with self.assertRaisesRegex(ValueError, "no assistant"):
                await original.run("hello")
            self.assertEqual(original.store.get_run(original.run_id)["status"], "error")

    async def test_missing_previous_anchor_marks_second_turn_error(self):
        async def fake_query(prompt, options):
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result="ok")

        transcripts = iter([
            [types.SimpleNamespace(uuid="assistant-1", type="assistant")],
            [types.SimpleNamespace(uuid="assistant-2", type="assistant")]])
        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", side_effect=lambda *args, **kwargs: next(transcripts)):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            await original.run("first")
            with self.assertRaisesRegex(ValueError, "previous Claude turn anchor"):
                await original.run("second")
            self.assertEqual(original.store.get_run(original.run_id)["status"], "error")

    async def test_factory_failure_marks_capture_error(self):
        with tempfile.TemporaryDirectory() as root, patch(
            "agentdbg.claude_sdk.load_factory", side_effect=ValueError("factory failed")):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            with self.assertRaisesRegex(ValueError, "factory failed"):
                await original.run("hello")
            self.assertEqual(original.store.get_run(original.run_id)["status"], "error")

    async def test_redacted_prompt_is_viewable_but_not_forkable(self):
        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            await hook({"tool_name": "Read", "tool_input": {"file_path": "x"},
                        "tool_response": "old"}, "id", {})
            yield AssistantMessage(content=[TextBlock(text="old")], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result="old")

        messages = lambda *args, **kwargs: [types.SimpleNamespace(uuid="assistant-1", type="assistant")]
        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True,
                                     redact_fields=("submitted_prompt",))
            await original.run("private prompt")
            self.assertFalse(original.store.get_run(original.run_id)["replayable"])
            with self.assertRaisesRegex(ValueError, "cannot be forked"):
                await fork_claude(original.store, original.run_id, 1, "new")

    async def test_native_fork_setup_failure_marks_run_error(self):
        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            await hook({"tool_name": "Read", "tool_input": {"file_path": "x"},
                        "tool_response": "old"}, "id", {})
            yield AssistantMessage(content=[TextBlock(text="old")], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result="old")

        count = 0
        def messages(*args, **kwargs):
            nonlocal count
            count += 1
            return [types.SimpleNamespace(uuid=f"assistant-{i}", type="assistant")
                    for i in range(1, count + 1)]
        with tempfile.TemporaryDirectory() as root, patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages), patch(
            "agentdbg.claude_sdk.fork_session", side_effect=ValueError("native fork failed")):
            original = ClaudeSession("tests.sdk_factories:claude_options", root, True)
            await original.run("first")
            await original.run("second")
            with self.assertRaisesRegex(ValueError, "native fork failed"):
                await fork_claude(original.store, original.run_id, 3, "new")
            self.assertEqual(original.store.list_runs()[-1]["status"], "error")
