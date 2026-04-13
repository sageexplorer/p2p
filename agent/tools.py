"""P2P API tools for LangGraph agent.

Each tool wraps a single API endpoint. The agent composes them
to execute full procurement workflows.
"""
import httpx
from langchain_core.tools import tool

BASE_URL = "http://localhost:8000"


def _post(path: str, json: dict | None = None) -> dict:
    resp = httpx.post(f"{BASE_URL}{path}", json=json)
    return resp.json()


def _get(path: str) -> dict:
    resp = httpx.get(f"{BASE_URL}{path}")
    return resp.json()


# ── Purchase Orders ──────────────────────────────────────────────


@tool
def create_purchase_order(vendor_id: int, line_items: list[dict]) -> dict:
    """Create a draft purchase order.

    Args:
        vendor_id: ID of the vendor (must be active).
        line_items: List of items, each with keys: sku, description, qty_ordered, unit_cost.
            Example: [{"sku": "SKU-1001", "description": "2x4x8 Pine Stud", "qty_ordered": 100, "unit_cost": "4.25"}]

    Returns:
        The created PO with status DRAFT, a workflow_id UUID, and line items.
        On error: structured error with error_code INACTIVE_VENDOR and next_actions.
    """
    return _post("/purchase-orders", {"vendor_id": vendor_id, "line_items": line_items})


@tool
def get_purchase_order(po_id: int) -> dict:
    """Get purchase order details including line items with receipt status.

    Args:
        po_id: Purchase order ID.
    """
    return _get(f"/purchase-orders/{po_id}")


@tool
def submit_purchase_order(po_id: int) -> dict:
    """Submit a DRAFT purchase order. Idempotent — safe to call twice.

    Args:
        po_id: Purchase order ID. Must be in DRAFT or SUBMITTED status.

    Returns:
        PO with status SUBMITTED. Error INVALID_STATUS_TRANSITION if PO is past SUBMITTED.
    """
    return _post(f"/purchase-orders/{po_id}/submit")


@tool
def receive_goods(po_id: int, received_by: str, line_items: list[dict]) -> dict:
    """Record a goods receipt against a submitted PO.

    Args:
        po_id: Purchase order ID.
        received_by: Name of the person receiving goods.
        line_items: List of received items, each with keys: po_line_item_id, qty_received.
            Example: [{"po_line_item_id": 1, "qty_received": 100}]

    Returns:
        GoodsReceipt object. PO auto-transitions to RECEIVED when all lines are fully received.
        Error OVER_RECEIPT if qty_received exceeds remaining capacity.
    """
    return _post(f"/purchase-orders/{po_id}/receive", {
        "received_by": received_by,
        "line_items": line_items,
    })


# ── Invoices ─────────────────────────────────────────────────────


@tool
def create_invoice(vendor_id: int, po_id: int, invoice_number: str, amount: str) -> dict:
    """Create an invoice against a purchase order.

    Args:
        vendor_id: Vendor ID.
        po_id: Purchase order ID the invoice is for.
        invoice_number: Unique invoice reference (e.g. "INV-2024-001").
        amount: Invoice amount as a decimal string (e.g. "1350.00"). Never use float.
    """
    return _post("/invoices", {
        "vendor_id": vendor_id,
        "po_id": po_id,
        "invoice_number": invoice_number,
        "amount": amount,
    })


@tool
def match_invoice(invoice_id: int) -> dict:
    """Run 3-way match: verifies invoice amount <= received goods value.

    Args:
        invoice_id: Invoice ID.

    Returns:
        Invoice with status MATCHED if amounts tie out.
        If partial receipt is pending, next_actions will include "partial_receipt_pending".
        Error AMOUNT_EXCEEDS_RECEIVED with context showing the gap and pending lines.
    """
    return _post(f"/invoices/{invoice_id}/match")


@tool
def approve_invoice(invoice_id: int) -> dict:
    """Approve a matched invoice and post GL entries. Idempotent.

    Args:
        invoice_id: Invoice ID. Must be in MATCHED status.

    Returns:
        Invoice with status APPROVED and gl_entries (debit AP Control 2000, credit vendor expense).
        Error INVOICE_NOT_MATCHED if invoice hasn't been matched yet.
    """
    return _post(f"/invoices/{invoice_id}/approve")


# ── Vendors ──────────────────────────────────────────────────────


@tool
def list_vendors() -> dict:
    """List all vendors with their payment terms and active status."""
    return _get("/vendors")


@tool
def get_vendor_exposure(vendor_id: int) -> dict:
    """Get AP exposure for a vendor: outstanding AP, credit limit, remaining credit.

    Args:
        vendor_id: Vendor ID.
    """
    return _get(f"/vendors/{vendor_id}/exposure")


ALL_TOOLS = [
    create_purchase_order,
    get_purchase_order,
    submit_purchase_order,
    receive_goods,
    create_invoice,
    match_invoice,
    approve_invoice,
    list_vendors,
    get_vendor_exposure,
]
