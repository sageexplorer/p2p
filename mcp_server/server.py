"""MCP Server exposing the P2P API as tools.

Each API endpoint becomes an MCP tool that Claude (or any MCP client)
can call directly. The server proxies to the running FastAPI app.

Run:
    .venv/bin/python -m mcp_server.server

Requires the P2P API running on localhost:8000.
"""
import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "http://localhost:8000"

mcp = FastMCP(
    "P2P Procurement API",
    instructions=(
        "You have access to a Purchase-to-Pay procurement system. "
        "Use these tools to manage purchase orders, goods receipts, invoices, "
        "and vendor exposure. Follow the workflow: create PO → submit → receive → "
        "invoice → match → approve. Read error responses carefully — they contain "
        "next_actions telling you how to recover."
    ),
)


def _api(method: str, path: str, body: dict | None = None) -> dict:
    """Call the P2P API and return the parsed response."""
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json=body)

    data = resp.json()

    # If error, format it clearly for the LLM
    if resp.status_code >= 400:
        return {
            "success": False,
            "http_status": resp.status_code,
            **data,
        }

    return {"success": True, "http_status": resp.status_code, **data}


# ── Vendor Tools ─────────────────────────────────────────────────


@mcp.tool()
def list_vendors() -> dict:
    """List all vendors with their payment terms, active status, and credit limits.

    Use this to find valid vendor IDs before creating a purchase order.
    Inactive vendors (is_active=false) cannot have new POs.
    """
    return _api("GET", "/vendors")


@mcp.tool()
def get_vendor(vendor_id: int) -> dict:
    """Get details for a specific vendor.

    Args:
        vendor_id: The vendor ID to look up.
    """
    return _api("GET", f"/vendors/{vendor_id}")


@mcp.tool()
def get_vendor_exposure(vendor_id: int) -> dict:
    """Get AP exposure for a vendor: total outstanding payables, credit limit, and remaining credit.

    Use this to check if a vendor is approaching their credit limit before creating new POs.

    Args:
        vendor_id: The vendor ID to check exposure for.
    """
    return _api("GET", f"/vendors/{vendor_id}/exposure")


# ── Purchase Order Tools ─────────────────────────────────────────


@mcp.tool()
def create_purchase_order(vendor_id: int, line_items: list[dict]) -> dict:
    """Create a new purchase order in DRAFT status.

    The vendor must be active (is_active=true), otherwise you'll get INACTIVE_VENDOR error.

    Args:
        vendor_id: ID of the vendor to order from. Must be active.
        line_items: List of items to order. Each item needs:
            - sku (str): Product SKU, e.g. "SKU-1001"
            - description (str): Item description
            - qty_ordered (int): Quantity to order
            - unit_cost (str): Cost per unit as decimal string, e.g. "4.25"

    Returns:
        The created PO with status=DRAFT, a workflow_id UUID, and all line items.

    Errors:
        INACTIVE_VENDOR (400): Vendor is not active. Try a different vendor.
        VENDOR_NOT_FOUND (404): Vendor ID doesn't exist. Use list_vendors to find valid IDs.
    """
    return _api("POST", "/purchase-orders", {
        "vendor_id": vendor_id,
        "line_items": line_items,
    })


@mcp.tool()
def get_purchase_order(po_id: int) -> dict:
    """Get full details of a purchase order including line items and receipt status.

    Shows qty_ordered vs qty_received for each line so you can track receipt progress.

    Args:
        po_id: Purchase order ID.
    """
    return _api("GET", f"/purchase-orders/{po_id}")


@mcp.tool()
def submit_purchase_order(po_id: int) -> dict:
    """Submit a DRAFT purchase order, changing its status to SUBMITTED.

    Idempotent: calling this on an already-SUBMITTED PO returns 200 with current state.
    Cannot submit a PO that is already RECEIVED or CLOSED (status only moves forward).

    Args:
        po_id: Purchase order ID to submit. Must be in DRAFT status.

    Errors:
        INVALID_STATUS_TRANSITION (409): PO is past SUBMITTED status. Check current_status in the error context.
    """
    return _api("POST", f"/purchase-orders/{po_id}/submit")


@mcp.tool()
def receive_goods(po_id: int, received_by: str, line_items: list[dict]) -> dict:
    """Record goods received against a submitted purchase order.

    Supports partial receipt: you can receive less than ordered and receive more later.
    PO auto-transitions to RECEIVED when ALL lines are fully received.

    Args:
        po_id: Purchase order ID (must be SUBMITTED or RECEIVED status).
        received_by: Name of person receiving the goods, e.g. "Warehouse Manager".
        line_items: Items being received. Each needs:
            - po_line_item_id (int): ID of the PO line item (from get_purchase_order)
            - qty_received (int): Quantity received in this delivery

    Returns:
        A GoodsReceipt with receipt details.

    Errors:
        OVER_RECEIPT (422): qty_received exceeds remaining capacity. Check context.max_receivable.
        INVALID_STATUS_TRANSITION (409): PO is not in SUBMITTED/RECEIVED status.
    """
    return _api("POST", f"/purchase-orders/{po_id}/receive", {
        "received_by": received_by,
        "line_items": line_items,
    })


# ── Invoice Tools ────────────────────────────────────────────────


@mcp.tool()
def create_invoice(vendor_id: int, po_id: int, invoice_number: str, amount: str) -> dict:
    """Create an invoice against a purchase order.

    The invoice starts in PENDING status. You must run match_invoice before approving.

    Args:
        vendor_id: Vendor ID.
        po_id: Purchase order ID this invoice is for.
        invoice_number: Unique invoice reference, e.g. "INV-2024-001".
        amount: Invoice amount as a decimal string, e.g. "1350.00". Never use float.
    """
    return _api("POST", "/invoices", {
        "vendor_id": vendor_id,
        "po_id": po_id,
        "invoice_number": invoice_number,
        "amount": amount,
    })


@mcp.tool()
def get_invoice(invoice_id: int) -> dict:
    """Get invoice details including GL entries (if approved).

    Args:
        invoice_id: Invoice ID.
    """
    return _api("GET", f"/invoices/{invoice_id}")


@mcp.tool()
def match_invoice(invoice_id: int) -> dict:
    """Run 3-way match: verify invoice amount <= total received goods value.

    Idempotent: calling on an already-MATCHED invoice returns 200.

    If the match succeeds but not all goods are received yet, the response will include
    next_actions=["partial_receipt_pending"] — this is informational, not a failure.

    Args:
        invoice_id: Invoice ID to match. Must be in PENDING status.

    Errors:
        AMOUNT_EXCEEDS_RECEIVED (422): Invoice is higher than received goods value.
            Read context.gap to see the difference.
            Read context.pending_lines to see what's still outstanding.
            Read next_actions for recovery options:
            - "wait_for_receipt": more goods are coming, retry match later
            - "reduce_invoice_amount": create a smaller invoice
            - "split_invoice": invoice only what's been received
    """
    return _api("POST", f"/invoices/{invoice_id}/match")


@mcp.tool()
def approve_invoice(invoice_id: int) -> dict:
    """Approve a matched invoice and post GL entries.

    Creates two GL entries:
    - Debit: AP Control account (2000)
    - Credit: Vendor's expense account

    Idempotent: re-approving returns 200 with existing GL entries (no duplicates).

    Args:
        invoice_id: Invoice ID. Must be in MATCHED status.

    Errors:
        INVOICE_NOT_MATCHED (409): Invoice hasn't been matched yet.
            Use match_invoice first.
    """
    return _api("POST", f"/invoices/{invoice_id}/approve")


# ── Observability Tools ──────────────────────────────────────────


@mcp.tool()
def get_audit_logs(workflow_id: str | None = None) -> dict:
    """Get audit trail events, optionally filtered by workflow_id.

    Every PO gets a workflow_id UUID at creation. All related events
    (submit, receive, invoice, match, approve, errors) are tagged with it.

    Args:
        workflow_id: Optional. Filter to events for this specific workflow.
    """
    path = "/api/audit-logs"
    if workflow_id:
        path += f"?workflow_id={workflow_id}"
    return _api("GET", path)


@mcp.tool()
def get_workflows() -> dict:
    """List all procurement workflows with event counts and timestamps.

    Each workflow represents a complete PO lifecycle. Use the workflow_id
    with get_audit_logs to see the full event timeline.
    """
    return _api("GET", "/api/workflows")


if __name__ == "__main__":
    mcp.run()
