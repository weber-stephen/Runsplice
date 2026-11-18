"""Interactive OpenAI Agents SDK capture. Requires OPENAI_API_KEY."""

import asyncio

from agents import Agent, function_tool

from agentdbg.openai_sdk import OpenAISession


@function_tool
def lookup_order(order_id: str) -> str:
    """Return the current status of an order."""
    return f"Order {order_id} is processing."


def make_agent():
    return Agent(name="Order helper", model="gpt-4.1-mini",
                 instructions="For every order question, call lookup_order before answering.",
                 tools=[lookup_order])


async def main():
    session = OpenAISession("examples.openai_live:make_agent", ".agentdbg/live", True,
                            read_only_tools=("lookup_order",))
    print(f"run {session.run_id}")
    while prompt := input("You (blank to finish): ").strip():
        result = await session.run(prompt)
        print(f"Agent: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
