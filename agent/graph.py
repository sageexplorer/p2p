"""LangGraph agent that operates the P2P API.

Uses a simple ReAct loop: the LLM decides which tool to call,
observes the result, and decides the next step.

Requires:
    - ANTHROPIC_API_KEY in .env file
    - P2P API running on localhost:8000
"""
from dotenv import load_dotenv
load_dotenv()

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from agent.tools import ALL_TOOLS

SYSTEM_PROMPT = """\
You are a procurement agent that manages the Purchase-to-Pay workflow.
You have access to a P2P API with tools for managing purchase orders, invoices, and vendors.

Workflow steps:
1. Create a draft purchase order (create_purchase_order)
2. Submit it (submit_purchase_order)
3. Receive goods (receive_goods)
4. Create an invoice (create_invoice)
5. Run 3-way match (match_invoice)
6. Approve the invoice (approve_invoice)

Rules:
- Always check vendor exposure before creating large POs.
- If a match fails with AMOUNT_EXCEEDS_RECEIVED, read the context and next_actions to decide what to do.
- If a partial_receipt_pending flag appears, note it but continue if amounts tie out.
- Money values are always decimal strings like "1350.00", never floats.
- Use the error_code and next_actions from any error response to guide your recovery.
"""


def create_agent():
    """Build and return the P2P procurement agent."""
    llm = ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)
    return create_react_agent(llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)


# Convenience: run directly with `python -m agent.graph`
if __name__ == "__main__":
    agent = create_agent()
    print("P2P Agent ready. Type a task (or 'quit' to exit).\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break

        result = agent.invoke({"messages": [("human", user_input)]})
        # Print the last AI message
        for msg in reversed(result["messages"]):
            if msg.type == "ai" and msg.content:
                print(f"\nAgent: {msg.content}\n")
                break
