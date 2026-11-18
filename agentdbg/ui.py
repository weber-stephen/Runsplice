"""Structured data and local browser UI for recorded offline examples."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import mimetypes
from pathlib import Path
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

from .report import analyze_context, failure_hint
from .sandbox import EXAMPLE_MODULES, ReplayController
from .store import RunStore


def _checkpoint(store, run_id, sequence):
    try:
        return store.checkpoint(run_id, sequence)
    except KeyError:
        return None


def _run_kind(run, steps):
    if not run["parent_id"]:
        return "Recorded run"
    if (run["override_applied"] or run["provider"] == "claude" or run["status"] == "override_not_applied" or
            any(step["sequence"] == run["fork_step"] and
                (step["origin"] == "overridden" or step["status"] == "unconfirmed") for step in steps)):
        return "Response test"
    return "Unchanged replay" if run["status"] == "replayed" else "Replay attempt"


def run_view(store, run_id):
    run = store.get_run(run_id)
    steps = []
    for step in store.get_steps(run_id):
        before = _checkpoint(store, run_id, step["sequence"] - 1)
        capture_limit = ("Requests, responses, and state were not captured." if not run["capture_content"] else
                         "This run contains omitted, redacted, or truncated data." if not run["replayable"] else None)
        checkpoint_limit = None if before and before["replayable"] else "No usable checkpoint before this step."
        replay_limit = ("Unchanged checkpoint replay is available only for offline examples."
                        if run["provider"] != "example" else capture_limit or checkpoint_limit)
        fork_limit = ("Select a step in the original recording to test another response." if run["parent_id"] else
                      "Claude model responses are view-only; select a tool result." if run["provider"] == "claude" and step["operation"] != "tool" else
                      "Response testing is not supported for this provider." if run["provider"] not in ("example", "openai", "claude") else
                      capture_limit or (checkpoint_limit if run["provider"] == "example" else None))
        steps.append({"sequence": step["sequence"], "operation": step["operation"],
                      "name": step["request"].get("name") if isinstance(step["request"], dict) else None,
                      "status": step["status"], "origin": step["origin"],
                      "limitation": step["limitation"],
                      "failure_hint": failure_hint(run, step),
                      "action_limits": {"replay": replay_limit, "fork": fork_limit},
                      "can_replay": replay_limit is None,
                      "can_fork": fork_limit is None})
    final = _checkpoint(store, run_id, len(steps)) if run["capture_content"] else None
    return {"run": run, "steps": steps,
            "final_answer": final["state"].get("answer") if final and final["replayable"] else None}


def step_view(store, run_id, sequence):
    steps = store.get_steps(run_id)
    selected = next((step for step in steps if step["sequence"] == sequence), None)
    if selected is None:
        raise KeyError(f"unknown step: {sequence}")
    before = _checkpoint(store, run_id, sequence - 1)
    after = _checkpoint(store, run_id, sequence)
    delta = None
    if before and after and before["replayable"] and after["replayable"]:
        old, new = before["state"], after["state"]
        delta = {key: {"before": old.get(key), "after": new.get(key),
                       **({"before_missing": True} if key not in old else {}),
                       **({"after_missing": True} if key not in new else {})}
                 for key in sorted(old.keys() | new.keys()) if key not in old or key not in new or old[key] != new[key]}
    context = None
    if selected["operation"] == "model":
        model_steps = [step for step in steps if step["operation"] == "model" and step["sequence"] <= sequence]
        context = analyze_context(model_steps)[-1]
    return {"step": selected, "request": selected["request"], "response": selected["response"],
            "state_delta": delta, "context": context,
            "failure_hint": failure_hint(store.get_run(run_id), selected)}


def compare_view(store, original_id, fork_id):
    fork_run = store.get_run(fork_id)
    if fork_run["parent_id"] != original_id:
        raise ValueError("runs are not an original and its fork")
    original, fork = store.get_steps(original_id), store.get_steps(fork_id)
    first = None
    for index, (old, new) in enumerate(zip(original, fork), 1):
        for kind in ("request", "response"):
            if old[kind] != new[kind]:
                first = {"sequence": index, "kind": kind}
                break
        if first:
            break
    if first is None and len(original) != len(fork):
        first = {"sequence": min(len(original), len(fork)) + 1, "kind": "length"}
    first_request = None
    for old, new in zip(original, fork):
        if old["limitation"] or new["limitation"] or new["operation"] == "system":
            break
        if old["operation"] != new["operation"] or old["request"] != new["request"]:
            first_request = new["sequence"]
            break
    answers = None
    if fork_run["status"] not in ("blocked", "override_not_applied"):
        old_final = _checkpoint(store, original_id, len(original))
        new_final = _checkpoint(store, fork_id, len(fork))
        if old_final and new_final:
            answers = {"original": old_final["state"].get("answer"),
                       "fork": new_final["state"].get("answer")}
    return {"original_id": original_id, "fork_id": fork_id, "fork_step": fork_run["fork_step"],
            "first_change": first, "first_changed_request": first_request,
            "outcome": fork_run["status"], "answer_comparison": answers,
            "override_applied": fork_run["override_applied"], "live_calls": fork_run["live_calls"]}


def make_server(root, host="127.0.0.1", port=0):
    if host != "127.0.0.1":
        raise ValueError("UI must bind to 127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # The URL contains a session token and captured data may be private.

        def respond(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def serve_file(self, path):
            relative = unquote(path.lstrip("/"))
            if ".." in relative.split("/") or not (relative.startswith("assets/") or relative.startswith("vendor/webawesome/")):
                self.respond(404, {"error": "not found"})
                return
            try:
                data = files("agentdbg").joinpath("web", *relative.split("/")).read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                self.respond(404, {"error": "not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(relative)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, action=False):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected or self.headers.get("X-Agentdbg-Token") != self.server.token:
                return False
            if action and self.headers.get("Origin") != f"http://{expected}":
                return False
            return True

        def store(self):
            return RunStore(self.server.store_root)

        def do_GET(self):
            route = urlsplit(self.path)
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") == expected and route.path == "/":
                if parse_qs(route.query).get("token") != [self.server.token]:
                    self.respond(403, {"error": "forbidden"})
                    return
                data = files("agentdbg").joinpath("web", "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' data:; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return
            if self.headers.get("Host") == expected and (route.path.startswith("/assets/") or route.path.startswith("/vendor/webawesome/")):
                self.serve_file(route.path)
                return
            if not self.authorized():
                self.respond(403, {"error": "forbidden"})
                return
            query = parse_qs(route.query)
            try:
                if route.path == "/api/examples":
                    value = {"examples": list(EXAMPLE_MODULES)}
                elif route.path == "/api/runs":
                    store = self.store()
                    runs = store.list_runs()
                    for run in runs:
                        run["kind"] = _run_kind(run, store.get_steps(run["id"]) if run["parent_id"] else [])
                    value = {"runs": runs}
                elif route.path == "/api/run":
                    value = run_view(self.store(), query["id"][0])
                elif route.path == "/api/step":
                    value = step_view(self.store(), query["id"][0], int(query["sequence"][0]))
                elif route.path == "/api/compare":
                    value = compare_view(self.store(), query["original"][0], query["fork"][0])
                else:
                    self.respond(404, {"error": "not found"})
                    return
                self.respond(200, value)
            except (KeyError, ValueError, PermissionError, OSError, IndexError) as exc:
                self.respond(400, {"error": str(exc)})

        def do_POST(self):
            if not self.authorized(action=True):
                self.respond(403, {"error": "forbidden"})
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= 1_100_000 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("invalid JSON request")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("request must be an object")
                route = urlsplit(self.path).path
                if route == "/api/record":
                    application = body.get("application")
                    capture = body.get("capture_content", False)
                    fields = body.get("redact_fields", [])
                    if not isinstance(application, str) or application not in EXAMPLE_MODULES or type(capture) is not bool or not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
                        raise ValueError("invalid bundled example or capture settings")
                    from .cli import record_example
                    run_id, _ = record_example(self.server.store_root, application, capture, fields)
                elif route == "/api/replay":
                    run_id = body.get("run_id")
                    step = body.get("from_step")
                    if not isinstance(run_id, str) or type(step) is not int:
                        raise ValueError("invalid replay request")
                    if self.store().get_run(run_id)["provider"] != "example":
                        raise ValueError("SDK runs do not support unchanged replay")
                    run_id = ReplayController(self.store()).replay(run_id, step)
                elif route == "/api/fork":
                    source = body.get("run_id")
                    step = body.get("step")
                    response = body.get("response")
                    live = body.get("continue_live", False)
                    if not isinstance(source, str) or type(step) is not int or type(live) is not bool:
                        raise ValueError("invalid fork request")
                    store = self.store()
                    selected = next((item for item in store.get_steps(source) if item["sequence"] == step), None)
                    if selected is None:
                        raise ValueError("unknown fork step")
                    run = store.get_run(source)
                    if run["provider"] == "example":
                        if not isinstance(response, dict):
                            raise ValueError("example response must be an object")
                        override = {"step": step, "operation": selected["operation"], "response": response}
                        run_id = ReplayController(store).replay(source, step, override)
                    elif run["provider"] == "openai":
                        from .openai_sdk import fork_openai
                        run_id = asyncio.run(fork_openai(store, source, step, response, live))
                    elif run["provider"] == "claude":
                        if not live:
                            raise ValueError("Claude fork requires live continuation")
                        from .claude_sdk import fork_claude
                        run_id = asyncio.run(fork_claude(store, source, step, response))
                    else:
                        raise ValueError("unsupported run provider")
                else:
                    self.respond(404, {"error": "not found"})
                    return
                self.respond(200, {"run_id": run_id})
            except (KeyError, ValueError, PermissionError, OSError, ImportError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(500, {"error": "SDK action failed; inspect the saved run"})

    class UiServer(ThreadingHTTPServer):
        request_queue_size = 64  # Web Awesome loads many local modules at once.

    server = UiServer((host, port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.store_root = Path(root)
    return server
