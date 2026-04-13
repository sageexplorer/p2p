"""LangGraph agent with tools auto-generated from the OpenAPI spec.

Instead of hand-writing tool wrappers, this fetches /openapi.json at startup
and creates tools automatically. The OpenAPI descriptions become the tool
docstrings that the LLM reasons over.

Run:
    .venv/bin/python -m agent.openapi_agent

Requires:
    - ANTHROPIC_API_KEY in .env
    - P2P API running on localhost:8000
"""
from dotenv import load_dotenv
load_dotenv()

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from agent.openapi_tools import build_tools_from_openapi

SYSTEM_PROMPT = """\
You are a procurement agent that manages the Purchase-to-Pay workflow.

Your tools were auto-generated from the P2P API's OpenAPI spec.
Each tool maps to one API endpoint.

Workflow steps:
1. list_vendors — find an active vendor
2. create_purchase_order — create a draft PO
3. submit_purchase_order — submit the PO
4. receive_goods — record goods received
5. create_invoice — create an invoice against the PO
6. match_invoice — run 3-way match (invoice <= received value)
7. approve_invoice — approve and post GL entries
8. get_vendor_exposure — check AP exposure

Rules:
- Read error responses carefully. The error_code and next_actions tell you what to do.
- Money is always a decimal string like "1350.00", never a float.
- Idempotent: submitting/matching/approving twice is safe.
- Status only moves forward: DRAFT → SUBMITTED → RECEIVED → CLOSED.
"""


def create_agent():
    tools = build_tools_from_openapi("http://localhost:8000")
    print (tools)
    # Clean up tool names (remove the verbose FastAPI suffixes)
    name_map = {
        "create_purchase_order_purchase_orders_post": "create_purchase_order",
        "get_purchase_order_purchase_orders__po_id__get": "get_purchase_order",
        "submit_purchase_order_purchase_orders__po_id__submit_post": "submit_purchase_order",
        "receive_goods_purchase_orders__po_id__receive_post": "receive_goods",
        "create_invoice_invoices_post": "create_invoice",
        "get_invoice_invoices__invoice_id__get": "get_invoice",
        "match_invoice_invoices__invoice_id__match_post": "match_invoice",
        "approve_invoice_invoices__invoice_id__approve_post": "approve_invoice",
        "list_vendors_vendors_get": "list_vendors",
        "get_vendor_vendors__vendor_id__get": "get_vendor",
        "get_vendor_exposure_vendors__vendor_id__exposure_get": "get_vendor_exposure",
        "get_audit_logs_api_audit_logs_get": "get_audit_logs",
        "get_workflows_api_workflows_get": "get_workflows",
    }
    for tool in tools:
        if tool.name in name_map:
            tool.name = name_map[tool.name]

    print(f"Loaded {len(tools)} tools from OpenAPI spec:")
    for t in tools:
        print(f"  - {t.name}")
    print()

    llm = ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)
    return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)


if __name__ == "__main__":
    agent = create_agent()
    print("P2P Agent ready (tools from OpenAPI). Type a task (or 'quit' to exit).\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break

        result = agent.invoke({"messages": [("human", user_input)]})
        for msg in reversed(result["messages"]):
            if msg.type == "ai" and msg.content:
                print(f"\nAgent: {msg.content}\n")
                break
