"""Interactive Claude Agent SDK capture. Requires Claude authentication."""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from agentdbg.claude_sdk import ClaudeSession


def make_options():
    return ClaudeAgentOptions(cwd=Path.cwd(), tools=["Read", "Glob", "Grep"],
                              allowed_tools=["Read", "Glob", "Grep"], setting_sources=[])


async def main():
    session = ClaudeSession("examples.claude_live:make_options", ".agentdbg/live", True)
    print(f"run {session.run_id}")
    while prompt := input("You (blank to finish): ").strip():
        result = await session.run(prompt)
        print(f"Agent: {result.result}")


if __name__ == "__main__":
    asyncio.run(main())
