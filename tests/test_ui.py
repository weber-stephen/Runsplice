import asyncio
import tempfile
import http.client
import json
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch

from agentdbg.cli import record_example
from agentdbg.sandbox import linux_docker_available
from agentdbg.store import RunStore
from agentdbg.ui import compare_view, make_server, run_view, step_view


class UiDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "store"

    def test_record_uses_registered_examples_only(self):
        invoice, _ = record_example(self.root, "example", True, ())
        triage, _ = record_example(self.root, "triage", True, ())
        store = RunStore(self.root)
        self.assertEqual([store.get_run(run)["application"] for run in (invoice, triage)],
                         ["example", "triage"])
        with self.assertRaisesRegex(ValueError, "bundled example"):
            record_example(self.root, "untrusted", True, ())
        self.assertEqual(len(store.list_runs()), 2)

    def test_step_view_reports_context_and_replay_eligibility(self):
        run, _ = record_example(self.root, "example", True, ())
        store = RunStore(self.root)
        view = run_view(store, run)
        self.assertEqual(len(view["steps"]), 4)
        self.assertTrue(view["steps"][1]["can_replay"])
        self.assertIn("stale invoice", view["steps"][1]["failure_hint"])
        self.assertEqual(view["steps"][1]["action_limits"], {"replay": None, "fork": None})
        detail = step_view(store, run, 4)
        self.assertEqual(detail["response"]["answer"], "unpaid")
        self.assertEqual(detail["context"]["input_tokens"], 42)
        self.assertEqual(detail["state_delta"]["answer"], {"before": None, "after": "unpaid"})
        self.assertIn("stale invoice", step_view(store, run, 2)["failure_hint"])

    def test_compare_aligns_changed_response_and_blocked_path(self):
        original, _ = record_example(self.root, "example", True, ())
        store = RunStore(self.root)
        fork = store.create_run("example", True, original, 2)
        original_steps = store.get_steps(original)
        store.append_step(fork, original_steps[0])
        changed = dict(original_steps[1], response={"invoice_id": "INV-42", "status": "paid", "as_of": "2025-01-01"}, origin="overridden")
        store.append_step(fork, changed)
        store.append_step(fork, {"operation": "tool", "request": {"name": "read_policy"},
                                 "response": {"blocked": "divergence"}, "status": "blocked", "origin": "blocked"})
        store.set_status(fork, "blocked")
        view = compare_view(store, original, fork)
        self.assertEqual(view["first_change"], {"sequence": 2, "kind": "response"})
        self.assertEqual(view["fork_step"], 2)
        self.assertEqual(view["outcome"], "blocked")
        self.assertIsNone(view["answer_comparison"])
        self.assertIsNone(step_view(store, fork, 2)["failure_hint"])
        self.assertEqual(view["first_changed_request"], 3)

    def test_metadata_only_run_cannot_replay(self):
        run, _ = record_example(self.root, "delivery", False, ())
        view = run_view(RunStore(self.root), run)
        self.assertFalse(view["run"]["replayable"])
        self.assertTrue(all(not step["can_replay"] for step in view["steps"]))
        self.assertIn("not captured", view["steps"][0]["action_limits"]["fork"])
        self.assertIsNone(step_view(RunStore(self.root), run, 2)["state_delta"])

    def test_state_changes_distinguish_missing_null_and_removed_fields(self):
        store = RunStore(self.root)
        run = store.create_run("example", True)
        store.save_checkpoint(run, 0, {"removed": None, "same": None}, store.workdir(run))
        store.append_step(run, {"operation": "tool", "request": {}, "response": None})
        store.save_checkpoint(run, 1, {"added": None, "same": None}, store.workdir(run))
        delta = step_view(store, run, 1)["state_delta"]
        self.assertEqual(delta["removed"], {"before": None, "after": None, "after_missing": True})
        self.assertEqual(delta["added"], {"before": None, "after": None, "before_missing": True})
        self.assertNotIn("same", delta)

    def test_action_limits_match_checkpoint_provider_and_derived_run_eligibility(self):
        store = RunStore(self.root)
        for provider, operation, can_fork in (("example", "tool", False), ("openai", "model", True),
                                               ("claude", "model", False), ("claude", "tool", True)):
            with self.subTest(provider=provider, operation=operation):
                run = store.create_run(provider, True, provider=provider)
                store.append_step(run, {"operation": operation, "request": {}, "response": {}})
                step = run_view(store, run)["steps"][0]
                self.assertFalse(step["can_replay"])
                self.assertEqual(step["can_fork"], can_fork)
                self.assertEqual(step["action_limits"]["fork"] is None, can_fork)
                self.assertIn("checkpoint" if provider == "example" else "offline examples", step["action_limits"]["replay"])
        child = store.create_run("example", True, run, 1)
        store.append_step(child, {"operation": "tool", "request": {}, "response": {}})
        self.assertIn("original recording", run_view(store, child)["steps"][0]["action_limits"]["fork"])

    def test_limited_capture_does_not_claim_a_first_changed_request(self):
        store = RunStore(self.root, frozenset({"private"}))
        original = store.create_run("example", True)
        store.append_step(original, {"operation": "tool", "request": {"private": "old"}, "response": {}})
        child = store.create_run("example", True, original, 1)
        store.append_step(child, {"operation": "tool", "request": {"private": "new"}, "response": {}})
        step = run_view(store, original)["steps"][0]
        self.assertFalse(step["can_fork"])
        self.assertIn("redacted", step["action_limits"]["fork"])
        self.assertIsNone(compare_view(store, original, child)["first_changed_request"])

    def test_changed_request_is_separate_from_edited_response_and_environment_block(self):
        run, _ = record_example(self.root, "example", True, ())
        store = RunStore(self.root)
        original = store.get_steps(run)
        child = store.create_run("example", True, run, 2)
        for step in original:
            changed = dict(step)
            if step["sequence"] == 2:
                changed.update(response={"status": "paid"}, origin="overridden")
            if step["sequence"] == 4:
                changed.update(request={"messages": [{"role": "tool", "content": "paid"}]}, status="blocked")
            store.append_step(child, changed)
        comparison = compare_view(store, run, child)
        self.assertEqual(comparison["first_change"], {"sequence": 2, "kind": "response"})
        self.assertEqual(comparison["first_changed_request"], 4)
        failed = store.create_run("example", True, run, 1)
        store.append_step(failed, {"operation": "system", "request": {"from": 1}, "response": {"blocked": "Docker unavailable"}, "status": "blocked"})
        self.assertIsNone(compare_view(store, run, failed)["first_changed_request"])


class UiHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "store"
        self.server = make_server(self.root)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def request(self, method, path, data=None, token=True, origin=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if token:
            headers["X-Agentdbg-Token"] = self.server.token
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{self.port}"
        body = json.dumps(data).encode() if data is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload) if payload and response.getheader("Content-Type") == "application/json" else payload

    def test_api_denies_missing_token_and_cross_origin(self):
        self.assertEqual(self.request("GET", "/api/runs", token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/record", {"application": "example"}, origin=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/record", {"application": "example"}, origin=False, token=False)[0], 403)
        self.assertEqual(len(RunStore(self.root).list_runs()), 0)

    def test_openai_sdk_run_can_be_viewed_and_forked(self):
        try:
            from agentdbg.openai_sdk import OpenAISession
        except ImportError:
            self.skipTest("OpenAI SDK extra unavailable")
        session = OpenAISession("tests.sdk_factories:openai_agent", self.root, True,
                                read_only_tools=("invoice_status",))
        asyncio.run(session.run("Is invoice paid?"))
        status, view = self.request("GET", f"/api/run?id={session.run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(view["final_answer"], "unpaid")
        self.assertTrue(view["steps"][1]["can_fork"])
        status, result = self.request("POST", "/api/fork", {"run_id": session.run_id, "step": 2,
            "response": "paid", "continue_live": True})
        self.assertEqual(status, 200, result)
        status, comparison = self.request("GET", f"/api/compare?original={session.run_id}&fork={result['run_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(comparison["answer_comparison"], {"original": "unpaid", "fork": "paid"})

    def test_claude_sdk_fork_requires_live_flag_and_compares_answer(self):
        try:
            from agentdbg.claude_sdk import ClaudeSession
            from claude_agent_sdk import AssistantMessage, ResultMessage, ToolResultBlock, UserMessage
            from claude_agent_sdk.types import TextBlock
        except ImportError:
            self.skipTest("Claude SDK extra unavailable")

        async def fake_query(prompt, options):
            hook = options.hooks["PostToolUse"][0].hooks[0]
            update = await hook({"tool_name": "Read", "tool_input": {"file_path": "x"},
                                 "tool_response": "unpaid", "tool_use_id": "call-1"}, "id", {})
            answer = update.get("hookSpecificOutput", {}).get("updatedToolOutput", "unpaid")
            yield UserMessage(content=[ToolResultBlock(tool_use_id="call-1", content=answer)])
            yield AssistantMessage(content=[TextBlock(text=answer)], model="claude-test")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="session", result=answer)

        messages = lambda *args, **kwargs: [types.SimpleNamespace(uuid="assistant-1", type="assistant")]
        with patch("agentdbg.claude_sdk.query", fake_query), patch(
            "agentdbg.claude_sdk.get_session_messages", messages):
            session = ClaudeSession("tests.sdk_factories:claude_options", self.root, True)
            asyncio.run(session.run("Check invoice"))
            source = session.store.get_steps(session.run_id)
            request = {"run_id": session.run_id, "step": 1, "response": "paid"}
            self.assertEqual(self.request("POST", "/api/fork", request)[0], 400)
            status, result = self.request("POST", "/api/fork", {**request, "continue_live": True})
            self.assertEqual(status, 200, result)
            comparison = self.request("GET", f"/api/compare?original={session.run_id}&fork={result['run_id']}")[1]
            self.assertEqual(comparison["answer_comparison"], {"original": "unpaid", "fork": "paid"})
            self.assertTrue(comparison["override_applied"])
            self.assertEqual(session.store.get_steps(session.run_id), source)

    def test_record_endpoint_accepts_registry_id_with_capture_off_default(self):
        status, result = self.request("POST", "/api/record", {"application": "triage"})
        self.assertEqual(status, 200)
        run = RunStore(self.root).get_run(result["run_id"])
        self.assertEqual(run["application"], "triage")
        self.assertFalse(run["capture_content"])
        self.assertEqual(self.request("GET", "/api/run?id=" + result["run_id"])[1]["run"]["id"], result["run_id"])

    def test_playable_invoice_demo_explicitly_captures_steps(self):
        status, result = self.request("POST", "/api/record", {"application": "example", "capture_content": True, "redact_fields": []})
        self.assertEqual(status, 200)
        view = self.request("GET", "/api/run?id=" + result["run_id"])[1]
        self.assertTrue(view["run"]["replayable"])
        self.assertEqual(len(view["steps"]), 4)
        self.assertEqual(self.request("GET", f"/api/step?id={result['run_id']}&sequence=4")[1]["response"]["answer"], "unpaid")

    def test_unknown_application_and_bad_fork_do_not_create_runs(self):
        self.assertEqual(self.request("POST", "/api/record", {"application": "other"})[0], 400)
        run, _ = record_example(self.root, "delivery", True, ())
        count = len(RunStore(self.root).list_runs())
        self.assertEqual(self.request("POST", "/api/fork", {"run_id": run, "step": 2, "response": {}})[0], 400)
        self.assertEqual(len(RunStore(self.root).list_runs()), count)

    def test_malformed_application_is_rejected_without_crashing_server(self):
        status, _ = self.request("POST", "/api/record", {"application": ["example"]})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("GET", "/api/examples")[0], 200)

    def test_metadata_only_replay_is_rejected_before_fork_creation(self):
        run, _ = record_example(self.root, "example", False, ())
        count = len(RunStore(self.root).list_runs())
        status, result = self.request("POST", "/api/replay", {"run_id": run, "from_step": 1})
        self.assertEqual(status, 400)
        self.assertIn("replay limitation", result["error"])
        self.assertEqual(len(RunStore(self.root).list_runs()), count)

    def test_get_step_and_compare_use_structured_data(self):
        run, _ = record_example(self.root, "example", True, ())
        status, detail = self.request("GET", f"/api/step?id={run}&sequence=4")
        self.assertEqual(status, 200)
        self.assertEqual(detail["response"]["answer"], "unpaid")
        self.assertEqual(self.request("GET", "/api/examples")[1]["examples"],
                         ["example", "triage", "delivery"])

    def test_run_list_distinguishes_response_tests_replays_and_execution_attempts(self):
        store = RunStore(self.root)
        original = store.create_run("example", True)
        store.append_step(original, {"operation": "model", "request": {}, "response": {"answer": "old"}})
        children = []
        for origin, status in (("overridden", "replayed"), ("recorded", "replayed"), ("blocked", "blocked")):
            child = store.create_run("example", True, original, 1)
            store.append_step(child, {"operation": "model", "request": {}, "response": {}, "origin": origin})
            store.set_status(child, status)
            children.append(child)
        runs = {run["id"]: run for run in self.request("GET", "/api/runs")[1]["runs"]}
        self.assertEqual(runs[original]["kind"], "Recorded run")
        self.assertEqual([runs[child]["kind"] for child in children], ["Response test", "Unchanged replay", "Replay attempt"])

    def test_nested_replay_does_not_inherit_response_test_label(self):
        original, _ = record_example(self.root, "example", True, ())
        store = RunStore(self.root)
        steps = store.get_steps(original)
        response_test = store.create_run("example", True, original, 2)
        for step in steps[:3]:
            item = dict(step)
            if item["sequence"] == 2:
                item.update(response={**item["response"], "status": "paid"}, origin="overridden")
            store.append_step(response_test, item)
        store.save_checkpoint(response_test, 3, store.checkpoint(original, 3)["state"], store.workdir(response_test))
        store.append_step(response_test, {**steps[3], "response": {"blocked": "changed request"},
                                          "status": "blocked", "origin": "blocked"})
        store.set_status(response_test, "blocked")
        self.assertTrue(run_view(store, response_test)["steps"][3]["can_replay"])
        with patch("agentdbg.sandbox.linux_docker_available", return_value=False):
            status, result = self.request("POST", "/api/replay", {"run_id": response_test, "from_step": 4})
        self.assertEqual(status, 200)
        replay = result["run_id"]
        self.assertEqual(store.get_run(replay)["parent_id"], response_test)
        self.assertEqual(store.get_steps(replay)[1]["origin"], "overridden")
        comparison = self.request("GET", f"/api/compare?original={response_test}&fork={replay}")[1]
        self.assertEqual(comparison["fork_step"], 4)
        self.assertEqual(comparison["outcome"], "blocked")
        completed = store.create_run("example", True, response_test, 4)
        for step in store.get_steps(response_test)[:3] + [steps[3]]:
            store.append_step(completed, step)
        store.set_status(completed, "replayed")
        runs = {run["id"]: run for run in self.request("GET", "/api/runs")[1]["runs"]}
        self.assertEqual(runs[response_test]["kind"], "Response test")
        self.assertEqual(runs[replay]["kind"], "Replay attempt")
        self.assertEqual(runs[completed]["kind"], "Unchanged replay")

    def test_page_and_component_assets_are_local(self):
        self.assertEqual(self.request("GET", "/", token=False)[0], 403)
        status, page = self.request("GET", "/?token=" + self.server.token, token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"/assets/app.js", page)
        self.assertIn(b"/assets/favicon.svg", page)
        self.assertIn(b"/vendor/webawesome/styles/webawesome.css", page)
        self.assertIn(b"/vendor/webawesome/components/button/button.js", page)
        for path in ("/assets/app.js", "/assets/app.css", "/assets/favicon.svg", "/vendor/webawesome/components/button/button.js"):
            status, data = self.request("GET", path, token=False)
            self.assertEqual(status, 200)
            self.assertTrue(data)
            if path.startswith("/assets/"):
                self.assertNotIn(b"https://", data)
        self.assertEqual(self.request("GET", "/assets/../privacy.py", token=False)[0], 404)

    def test_capture_text_is_data_and_not_inserted_in_html(self):
        store = RunStore(self.root)
        run = store.create_run("example", True)
        marker = '<img src=x onerror="alert(1)">'
        store.append_step(run, {"operation": "tool", "request": {"name": "lookup_customer"},
                                "response": {"content": marker}})
        page = self.request("GET", "/?token=" + self.server.token, token=False)[1]
        detail = self.request("GET", f"/api/step?id={run}&sequence=1")[1]
        self.assertNotIn(marker.encode(), page)
        self.assertEqual(detail["response"]["content"], marker)

    @unittest.skipUnless(linux_docker_available(), "Linux Docker engine unavailable")
    def test_invoice_http_fork_shows_blocked_divergence(self):
        original = self.request("POST", "/api/record", {"application": "example", "capture_content": True})[1]["run_id"]
        before = RunStore(self.root).get_steps(original)
        response = dict(self.request("GET", f"/api/step?id={original}&sequence=2")[1]["response"], status="paid")
        status, result = self.request("POST", "/api/fork", {"run_id": original, "step": 2, "response": response})
        self.assertEqual(status, 200)
        comparison = self.request("GET", f"/api/compare?original={original}&fork={result['run_id']}")[1]
        self.assertEqual(comparison["first_change"], {"sequence": 2, "kind": "response"})
        self.assertEqual(comparison["outcome"], "blocked")
        self.assertIsNone(comparison["answer_comparison"])
        self.assertEqual(RunStore(self.root).get_steps(original), before)

    @unittest.skipUnless(linux_docker_available(), "Linux Docker engine unavailable")
    def test_delivery_http_fork_can_change_final_answer(self):
        original = self.request("POST", "/api/record", {"application": "delivery", "capture_content": True})[1]["run_id"]
        response = dict(self.request("GET", f"/api/step?id={original}&sequence=2")[1]["response"], answer="delivered")
        status, result = self.request("POST", "/api/fork", {"run_id": original, "step": 2, "response": response})
        self.assertEqual(status, 200)
        comparison = self.request("GET", f"/api/compare?original={original}&fork={result['run_id']}")[1]
        self.assertEqual(comparison["outcome"], "replayed")
        self.assertEqual(comparison["answer_comparison"], {"original": "in transit", "fork": "delivered"})


if __name__ == "__main__":
    unittest.main()
