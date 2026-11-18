"""Capture and replay nonstreaming OpenAI Agents SDK sessions."""

from dataclasses import replace
import json
import time

from agents import FunctionTool, Runner, SQLiteSession
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from .privacy import canonical
from .sdk_common import load_factory, private_store


_response_adapter = TypeAdapter(ModelResponse)


class ReplayStopped(Exception):
    pass


class _RecordedModel(Model):
    def __init__(self, owner, original, contract):
        self.owner, self.original, self.contract = owner, original, contract

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, *, previous_response_id, conversation_id, prompt):
        request = to_jsonable_python({
            "system_instructions": system_instructions, "input": input,
            "model_settings": model_settings, "tools": [tool.name for tool in tools],
            "agent_contract": self.contract,
            "handoffs": [handoff.tool_name for handoff in handoffs],
            "output_schema": output_schema.json_schema() if output_schema else None,
            "previous_response_id": previous_response_id, "conversation_id": conversation_id,
            "prompt": prompt,
        })
        async def invoke():
            response = await self.original.get_response(system_instructions, input, model_settings,
                tools, output_schema, handoffs, tracing, previous_response_id=previous_response_id,
                conversation_id=conversation_id, prompt=prompt)
            return _response_adapter.dump_python(response, mode="json")
        saved = await self.owner.call("model", request, invoke)
        return _response_adapter.validate_python(saved)

    def stream_response(self, *args, **kwargs):
        raise ValueError("streaming is not supported by Runsplice capture")


class OpenAISession:
    """Use one instance for every turn of a new SDK conversation."""

    def __init__(self, factory, store, capture_content=False, redact_fields=(), read_only_tools=()):
        self.store = private_store(store, redact_fields)
        self.factory = factory
        self.read_only_tools = tuple(read_only_tools)
        self.run_id = self.store.create_run("openai", capture_content, provider="openai")
        self.turns = []
        self.session = SQLiteSession(self.run_id, ":memory:")
        try:
            self.agent = self._agent()
        except BaseException:
            self.store.set_status(self.run_id, "error")
            raise
        self._save_session()

    def _agent(self):
        agent = load_factory(self.factory)()
        if (agent.handoffs or agent.mcp_servers or agent.input_guardrails or agent.output_guardrails or
                agent.hooks or callable(agent.instructions) or agent.prompt is not None):
            raise ValueError("OpenAI capture supports a single agent without handoffs, MCP, guardrails, hooks, or dynamic prompts")
        if any(not isinstance(tool, FunctionTool) for tool in agent.tools):
            raise ValueError("OpenAI capture supports local function tools only")
        if any(tool.tool_input_guardrails or tool.tool_output_guardrails or tool.needs_approval or
               callable(tool.is_enabled) for tool in agent.tools):
            raise ValueError("OpenAI capture does not support tool guardrails, approvals, or dynamic enablement")
        model = agent.model if isinstance(agent.model, Model) else OpenAIProvider().get_model(agent.model)
        model_name = agent.model if isinstance(agent.model, str) else getattr(model, "model", None)
        model_identity = {"class": f"{type(model).__module__}.{type(model).__qualname__}",
                          "name": model_name if isinstance(model_name, str) else None}
        contract = {"model": model_identity, "tools": [
            {"name": tool.name, "description": tool.description,
             "parameters": tool.params_json_schema, "strict": tool.strict_json_schema}
            for tool in agent.tools]}
        tools = []
        for tool in agent.tools:
            async def wrapped(ctx, arguments, original=tool.on_invoke_tool, name=tool.name):
                async def invoke():
                    return to_jsonable_python(await original(ctx, arguments))
                return await self.call("tool", {"name": name, "arguments": json.loads(arguments)}, invoke)
            tools.append(replace(tool, on_invoke_tool=wrapped))
        return agent.clone(model=_RecordedModel(self, model, contract), tools=tools)

    def _save_session(self):
        self.store.set_session(self.run_id, {"factory": self.factory, "turns": self.turns,
                                             "read_only_tools": self.read_only_tools})

    async def call(self, operation, request, invoke):
        started = time.monotonic()
        step = {"operation": operation, "request": request, "origin": "recorded"}
        try:
            response = await invoke()
            step["response"] = response
            return response
        except BaseException as exc:
            step.update(response=None, status="error", error_type=type(exc).__name__, error_message=str(exc))
            raise
        finally:
            step["duration_ms"] = (time.monotonic() - started) * 1000
            self.store.append_step(self.run_id, step)

    async def run(self, prompt):
        if not isinstance(prompt, str):
            raise ValueError("OpenAI capture requires a text prompt")
        self.store.set_status(self.run_id, "running")
        self.turns.append(prompt)
        self._save_session()
        try:
            result = await Runner.run(self.agent, prompt, session=self.session)
            self.store.save_checkpoint(self.run_id, len(self.store.get_steps(self.run_id)),
                                       {"turn": len(self.turns), "answer": to_jsonable_python(result.final_output)},
                                       self.store.workdir(self.run_id))
            self.store.set_status(self.run_id, "complete")
            return result
        except BaseException:
            self.store.set_status(self.run_id, "error")
            raise


class _OpenAIFork(OpenAISession):
    def __init__(self, store, parent_id, step, response, continue_live):
        parent = store.get_run(parent_id)
        if parent["provider"] != "openai" or not parent["replayable"]:
            raise ValueError("OpenAI run cannot be forked")
        source = store.session(parent_id)
        steps = store.get_steps(parent_id)
        if not source or step < 1 or step > len(steps) or steps[step - 1]["operation"] not in ("model", "tool"):
            raise ValueError("invalid fork step")
        self.store = private_store(store.root, parent["redact_fields"])
        self.factory = source["factory"]
        self.read_only_tools = tuple(source["read_only_tools"])
        self.run_id = self.store.create_run("openai", True, parent_id, step, provider="openai")
        self.turns = []
        self.session = SQLiteSession(self.run_id, ":memory:")
        self.steps, self.cursor = steps, 0
        self.target, self.response, self.continue_live = step, response, continue_live
        self.applied = self.live_calls = 0
        self.live = False
        try:
            self.agent = self._agent()
        except BaseException:
            self.store.set_status(self.run_id, "error")
            raise

    async def call(self, operation, request, invoke):
        started = time.monotonic()
        expected = self.steps[self.cursor] if self.cursor < len(self.steps) else None
        matches = bool(expected and expected["operation"] == operation and
                       canonical(expected["request"]) == canonical(request))
        if not self.live and matches:
            self.cursor += 1
            response = self.response if self.cursor == self.target else expected["response"]
            origin = "overridden" if self.cursor == self.target else "recorded"
            if self.cursor == self.target:
                self.applied = 1
        elif not self.continue_live or not self.applied:
            self.store.append_step(self.run_id, {"operation": operation, "request": request,
                "response": {"blocked": "request diverged before live continuation"},
                "status": "blocked", "origin": "blocked"})
            raise ReplayStopped("request diverged before live continuation")
        else:
            self.live = True
            if operation == "tool" and request["name"] not in self.read_only_tools:
                self.store.append_step(self.run_id, {"operation": operation, "request": request,
                    "response": {"blocked": "tool is not declared read-only"},
                    "status": "blocked", "origin": "blocked"})
                raise ReplayStopped("tool is not declared read-only")
            try:
                response = await invoke()
            except BaseException as exc:
                self.store.append_step(self.run_id, {"operation": operation, "request": request,
                    "response": None, "status": "error", "origin": "live",
                    "error_type": type(exc).__name__, "error_message": str(exc),
                    "duration_ms": (time.monotonic() - started) * 1000})
                raise
            origin = "live"
            self.live_calls += 1
        self.store.append_step(self.run_id, {"operation": operation, "request": request,
                                             "response": response, "origin": origin,
                                             "duration_ms": (time.monotonic() - started) * 1000})
        return response


async def fork_openai(store, parent_id, step, response, continue_live=False):
    if not isinstance(response, (dict, list, str, int, float, bool)) and response is not None:
        raise ValueError("replacement must be JSON")
    parent = store.get_run(parent_id)
    if parent["provider"] != "openai" or not parent["replayable"]:
        raise ValueError("OpenAI run cannot be forked")
    steps = store.get_steps(parent_id)
    if step < 1 or step > len(steps):
        raise ValueError("invalid fork step")
    if steps[step - 1]["operation"] == "model":
        _response_adapter.validate_python(response)
    elif type(response) is not type(steps[step - 1]["response"]):
        raise ValueError("tool replacement must preserve the response type")
    fork = _OpenAIFork(store, parent_id, step, response, continue_live)
    source = store.session(parent_id)
    try:
        for prompt in source["turns"]:
            await fork.run(prompt)
        fork.store.set_status(fork.run_id, "live_complete" if fork.live else "replayed")
    except ReplayStopped:
        fork.store.set_status(fork.run_id, "blocked")
    except BaseException:
        fork.store.set_status(fork.run_id, "error")
        raise
    finally:
        fork.store.set_fork_result(fork.run_id, fork.applied, fork.live_calls)
    return fork.run_id
