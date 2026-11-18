"""Offline SDK factories used by adapter integration tests."""

def _answer(call):
    from agents.testing import assistant_message, function_call
    items = call.input if isinstance(call.input, list) else [call.input]
    last = items[-1]
    if isinstance(last, dict) and last.get("type") == "function_call_output":
        return [assistant_message(str(last["output"]))]
    if isinstance(last, dict) and last.get("role") == "user" and "again" in str(last.get("content")):
        return [assistant_message("follow-up")]
    return [function_call("invoice_status", {}, call_id="call-invoice")]


def openai_agent():
    from agents import Agent, function_tool
    from agents.testing import ModelStep, ScriptedModel

    @function_tool
    def invoice_status() -> str:
        """Look up the invoice status."""
        return "unpaid"

    return Agent(name="Invoice", model=ScriptedModel([ModelStep.respond(_answer) for _ in range(12)]),
                 tools=[invoice_status])


def claude_options():
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(model="claude-test", tools=["Read"], allowed_tools=["Read"],
                              setting_sources=[])


def claude_live_options():
    import os
    from pathlib import Path
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(cwd=Path(os.environ["AGENTDBG_TEST_CWD"]), tools=["Read"],
                              allowed_tools=["Read"], setting_sources=[])
