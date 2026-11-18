"""Capture Claude Agent SDK sessions and branch at a selected tool result."""

from dataclasses import replace

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
                              ToolResultBlock, UserMessage,
                              fork_session, get_session_messages, query)
from pydantic_core import to_jsonable_python

from .privacy import canonical
from .sdk_common import load_factory, private_store


READ_ONLY_TOOLS = ("Read", "Glob", "Grep", "WebSearch", "WebFetch")


def _permitted_read_only_tools(options):
    if not isinstance(options.tools, list):
        return []
    return [name for name in READ_ONLY_TOOLS if name in options.tools and
            name in options.allowed_tools and name not in options.disallowed_tools]


def _completed_turn_anchor(messages, previous):
    if previous is not None:
        position = next((i for i, message in enumerate(messages) if message.uuid == previous), None)
        if position is None:
            raise ValueError("previous Claude turn anchor is absent from the transcript")
        messages = messages[position + 1:]
    assistants = [message.uuid for message in messages if message.type == "assistant" and
                  isinstance(message.uuid, str) and message.uuid]
    if not assistants:
        raise ValueError("completed Claude turn has no assistant transcript message")
    return assistants[-1]


class ClaudeSession:
    """Use one instance for every turn of a new Claude SDK conversation."""

    def __init__(self, factory, store, capture_content=False, redact_fields=()):
        self.store = private_store(store, redact_fields)
        self.factory = factory
        self.run_id = self.store.create_run("claude", capture_content, provider="claude")
        self.turns, self.turn_end_ids = [], []
        self.session_id = None
        try:
            self.store.set_session(self.run_id, {"factory": factory, "turns": [], "turn_end_ids": []})
        except BaseException:
            self.store.set_status(self.run_id, "error")
            raise

    def _options(self, hook):
        options = load_factory(self.factory)()
        if not isinstance(options, ClaudeAgentOptions) or options.hooks:
            raise ValueError("Claude factory must return ClaudeAgentOptions without hooks")
        return replace(options, resume=self.session_id, session_id=None,
                       continue_conversation=False, fork_session=False,
                       hooks={"PostToolUse": [HookMatcher(hooks=[hook])]})

    async def _tool_hook(self, data, _tool_id, _context):
        self.store.append_step(self.run_id, {"operation": "tool",
            "request": {"name": data["tool_name"], "arguments": data["tool_input"], "turn": len(self.turns)},
            "response": to_jsonable_python(data.get("tool_response")), "origin": "recorded",
            "duration_ms": data.get("duration_ms")})
        return {}

    async def run(self, prompt):
        if not isinstance(prompt, str):
            raise ValueError("Claude capture requires a text prompt")
        self.store.set_status(self.run_id, "running")
        result = None
        try:
            self.turns.append(prompt)
            options = self._options(self._tool_hook)
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    self.store.append_step(self.run_id, {"operation": "model",
                        "request": {"turn": len(self.turns), "model": message.model,
                                    "submitted_prompt": prompt, "prior_prompts": self.turns[:-1],
                                    "system_prompt": to_jsonable_python(options.system_prompt),
                                    "observed_step_sequences": [item["sequence"] for item in
                                                                self.store.get_steps(self.run_id)]},
                        "response": to_jsonable_python(message), "origin": "recorded"})
                elif isinstance(message, ResultMessage):
                    result = message
                    self.session_id = message.session_id
            if result is None:
                raise ValueError("Claude session ended without a result")
            if result.is_error:
                raise ValueError("Claude session failed; inspect the captured steps")
            messages = get_session_messages(self.session_id, directory=options.cwd)
            self.turn_end_ids.append(_completed_turn_anchor(messages,
                self.turn_end_ids[-1] if self.turn_end_ids else None))
            self.store.set_session(self.run_id, {"factory": self.factory, "turns": self.turns,
                                                 "turn_end_ids": self.turn_end_ids,
                                                 "sdk_session_id": self.session_id,
                                                 "cwd": str(options.cwd) if options.cwd is not None else None})
            self.store.save_checkpoint(self.run_id, len(self.store.get_steps(self.run_id)),
                                       {"turn": len(self.turns), "answer": result.result},
                                       self.store.workdir(self.run_id))
            self.store.set_status(self.run_id, "complete")
            return result
        except BaseException:
            self.store.set_status(self.run_id, "error")
            raise


async def fork_claude(store, parent_id, step, response):
    parent = store.get_run(parent_id)
    if parent["provider"] != "claude" or not parent["replayable"]:
        raise ValueError("Claude run cannot be forked")
    source = store.session(parent_id)
    steps = store.get_steps(parent_id)
    if not source or step < 1 or step > len(steps) or steps[step - 1]["operation"] != "tool":
        raise ValueError("Claude forks require a captured tool step")
    selected = steps[step - 1]
    original = selected["response"]
    if type(response) is not type(original) or (isinstance(original, dict) and
                                                 any(key not in response for key in original)):
        raise ValueError("replacement must preserve the tool output shape")
    initial_options = load_factory(source["factory"])()
    if not isinstance(initial_options, ClaudeAgentOptions) or initial_options.hooks:
        raise ValueError("Claude factory must return ClaudeAgentOptions without hooks")
    permitted = _permitted_read_only_tools(initial_options)
    if selected["request"]["name"] not in permitted:
        raise ValueError("selected Claude tool is not permitted for a read-only fork")
    target_turn = selected["request"]["turn"]
    fork = ClaudeSession.__new__(ClaudeSession)
    fork.store = private_store(store.root, parent["redact_fields"])
    fork.factory = source["factory"]
    fork.run_id = fork.store.create_run("claude", True, parent_id, step, provider="claude")
    try:
        fork.turns = source["turns"][:target_turn - 1]
        fork.turn_end_ids = source["turn_end_ids"][:target_turn - 1]
        fork.session_id = None
        if target_turn > 1:
            prefix = [item for item in steps if item["request"].get("turn", 0) < target_turn]
            for item in prefix:
                fork.store.append_step(fork.run_id, item)
            prior_checkpoint = store.checkpoint(parent_id, len(prefix))
            fork.store.save_checkpoint(fork.run_id, len(prefix), prior_checkpoint["state"],
                                       fork.store.workdir(fork.run_id))
            prior = source["turn_end_ids"][target_turn - 2]
            if not prior:
                raise ValueError("Claude session has no prior completed turn")
            fork.session_id = fork_session(source["sdk_session_id"], directory=source["cwd"],
                                           up_to_message_id=prior).session_id
    except BaseException:
        fork.store.set_status(fork.run_id, "error")
        raise
    applied = False
    pending = None
    pending_steps = []
    unsafe_attempted = False
    occurrence = 0
    target_occurrence = sum(1 for item in steps[:step] if item["operation"] == "tool" and
        item["request"] == selected["request"])

    async def guard(data, _tool_id, _context):
        nonlocal unsafe_attempted
        if data["tool_name"] not in permitted:
            unsafe_attempted = True
            pending_steps.append({"operation": "tool",
                "request": {"name": data["tool_name"], "arguments": data["tool_input"], "turn": len(fork.turns)},
                "response": {"blocked": "tool is not read-only"}, "status": "blocked", "origin": "blocked"})
            flush_steps()
            return {"continue_": False, "stopReason": "Runsplice live fork permits read-only tools only",
                    "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": "Runsplice live fork permits read-only tools only"}}
        return {}

    def flush_steps():
        while pending_steps and "tool_use_id" not in pending_steps[0]:
            fork.store.append_step(fork.run_id, pending_steps.pop(0))

    def flush_unconfirmed():
        nonlocal pending
        if pending is not None:
            pending.pop("tool_use_id")
            pending["status"] = "unconfirmed"
            pending = None
        flush_steps()

    async def replace_output(data, tool_id, context):
        nonlocal occurrence, pending
        request = {"name": data["tool_name"], "arguments": data["tool_input"], "turn": len(fork.turns)}
        matches = canonical(request) == canonical(selected["request"])
        if matches:
            occurrence += 1
        use_override = matches and occurrence == target_occurrence and not applied
        original_output = to_jsonable_python(data.get("tool_response"))
        if use_override:
            pending = {"operation": "tool", "request": request, "response": original_output,
                       "origin": "live", "duration_ms": data.get("duration_ms"),
                       "tool_use_id": data.get("tool_use_id")}
            pending_steps.append(pending)
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": response}}
        pending_steps.append({"operation": "tool", "request": request,
            "response": original_output, "origin": "live", "duration_ms": data.get("duration_ms")})
        flush_steps()
        return {}

    def confirm_output(message):
        nonlocal applied, pending
        if pending is None or not isinstance(message, UserMessage):
            return
        blocks = [item for item in message.content if isinstance(item, ToolResultBlock)] if isinstance(message.content, list) else []
        block = next((item for item in blocks if item.tool_use_id == pending["tool_use_id"]), None)
        if block is None:
            return
        raw_result = message.tool_use_result if len(blocks) == 1 else None
        observed = raw_result if raw_result is not None else block.content
        # shortcut: confirm exact streamed tool results; revisit if the SDK exposes an acceptance signal.
        accepted = block.content is not None and canonical(block.content) == canonical(response)
        if not accepted and raw_result is not None and canonical(block.content) != canonical(pending["response"]):
            accepted = canonical(raw_result) == canonical(response)
        pending["response"] = response if accepted else to_jsonable_python(observed)
        pending["origin"] = "overridden" if accepted else "live"
        pending.pop("tool_use_id")
        applied = accepted
        pending = None
        flush_steps()

    try:
        for prompt in source["turns"][target_turn - 1:]:
            fork.turns.append(prompt)
            options = load_factory(fork.factory)()
            if not isinstance(options, ClaudeAgentOptions) or options.hooks:
                raise ValueError("Claude factory must return ClaudeAgentOptions without hooks")
            permitted = _permitted_read_only_tools(options)
            options = replace(options, resume=fork.session_id, session_id=None, cwd=source["cwd"],
                tools=permitted, allowed_tools=permitted, mcp_servers={}, setting_sources=[],
                continue_conversation=False, fork_session=False,
                permission_mode="dontAsk", can_use_tool=None, plugins=[], agents=None,
                skills=[], add_dirs=[], extra_args={}, settings=None,
                hooks={"PreToolUse": [HookMatcher(hooks=[guard])],
                       "PostToolUse": [HookMatcher(hooks=[replace_output])]})
            result = None
            async for message in query(prompt=prompt, options=options):
                confirm_output(message)
                if isinstance(message, AssistantMessage):
                    flush_unconfirmed()
                    fork.store.append_step(fork.run_id, {"operation": "model",
                        "request": {"turn": len(fork.turns), "model": message.model,
                                    "submitted_prompt": prompt, "prior_prompts": fork.turns[:-1],
                                    "system_prompt": to_jsonable_python(options.system_prompt),
                                    "observed_step_sequences": [item["sequence"] for item in
                                                                fork.store.get_steps(fork.run_id)]},
                        "response": to_jsonable_python(message), "origin": "live"})
                elif isinstance(message, ResultMessage):
                    result = message
                    fork.session_id = message.session_id
            flush_unconfirmed()
            if unsafe_attempted:
                break
            if result is None:
                raise ValueError("Claude branch ended without a result")
            if result.is_error:
                raise ValueError("Claude branch failed; inspect the captured steps")
            fork.store.save_checkpoint(fork.run_id, len(fork.store.get_steps(fork.run_id)),
                                       {"turn": len(fork.turns), "answer": result.result},
                                       fork.store.workdir(fork.run_id))
        fork.store.set_status(fork.run_id, "blocked" if unsafe_attempted else
                              "live_complete" if applied else "override_not_applied")
        fork.store.set_fork_result(fork.run_id, applied,
            sum(item["origin"] == "live" for item in fork.store.get_steps(fork.run_id)))
    except BaseException:
        fork.store.set_status(fork.run_id, "error")
        raise
    return fork.run_id
