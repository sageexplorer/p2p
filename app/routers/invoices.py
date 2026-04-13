"""Invoice endpoints.

Covers invoice creation, 3-way matching, and approval with GL posting.
"""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..audit import log_event
from ..database import get_db
from ..enums import InvoiceStatus
from ..errors import P2PError
from ..models import GLEntry, Invoice, PurchaseOrder, Vendor
from ..schemas import InvoiceCreate, InvoiceRead

router = APIRouter(prefix="/invoices", tags=["invoices"])

DB = Annotated[Session, Depends(get_db)]

AP_CONTROL_ACCOUNT = "2000"


def _get_invoice_or_404(db: Session, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise P2PError(404, "INVOICE_NOT_FOUND",
                        f"Invoice {invoice_id} not found.",
                        context={"invoice_id": invoice_id},
                        next_actions=["list_invoices"])
    return inv


def _received_value(po: PurchaseOrder) -> Decimal:
    """Sum of qty_received * unit_cost across all PO lines."""
    return sum(
        Decimal(li.qty_received) * li.unit_cost for li in po.line_items
    )


def _pending_lines(po: PurchaseOrder) -> list[dict]:
    """Lines where qty_received < qty_ordered."""
    return [
        {
            "po_line_item_id": li.id,
            "sku": li.sku,
            "qty_ordered": li.qty_ordered,
            "qty_received": li.qty_received,
            "qty_outstanding": li.qty_ordered - li.qty_received,
        }
        for li in po.line_items
        if li.qty_received < li.qty_ordered
    ]


# ── Create invoice ───────────────────────────────────────────────

@router.post("", response_model=InvoiceRead, status_code=201)
def create_invoice(body: InvoiceCreate, db: DB):
    """Create an invoice against a purchase order."""
    po = db.get(PurchaseOrder, body.po_id)
    if not po:
        raise P2PError(404, "PO_NOT_FOUND",
                        f"Purchase order {body.po_id} not found.",
                        context={"po_id": body.po_id},
                        next_actions=["list_purchase_orders"])

    inv = Invoice(
        vendor_id=body.vendor_id,
        po_id=body.po_id,
        invoice_number=body.invoice_number,
        amount=body.amount,
    )
    db.add(inv)
    db.flush()
    log_event(db, action="create_invoice", entity_type="invoice", entity_id=inv.id,
              workflow_id=po.workflow_id,
              detail={"po_id": po.id, "amount": str(inv.amount)})
    db.commit()
    db.refresh(inv)
    return inv


# ── Get invoice detail ───────────────────────────────────────────

@router.get("/{invoice_id}", response_model=InvoiceRead)
def get_invoice(invoice_id: int, db: DB):
    """Retrieve an invoice with GL entries."""
    return _get_invoice_or_404(db, invoice_id)


# ── 3-way match ──────────────────────────────────────────────────

@router.post("/{invoice_id}/match", response_model=InvoiceRead)
def match_invoice(invoice_id: int, db: DB):
    """Run 3-way match: invoice amount must be <= received goods value."""
    inv = _get_invoice_or_404(db, invoice_id)

    if inv.status in (InvoiceStatus.MATCHED, InvoiceStatus.APPROVED):
        resp = InvoiceRead.model_validate(inv)
        if inv.status == InvoiceStatus.MATCHED:
            po = db.get(PurchaseOrder, inv.po_id)
            pending = _pending_lines(po)
            if pending:
                resp.next_actions = ["partial_receipt_pending"]
        return resp

    po = db.get(PurchaseOrder, inv.po_id)
    recv_val = _received_value(po)
    pending = _pending_lines(po)

    if inv.amount > recv_val:
        raise P2PError(422, "AMOUNT_EXCEEDS_RECEIVED",
                        f"Invoice amount {inv.amount} exceeds received goods value {recv_val}.",
                        context={
                            "invoice_amount": str(inv.amount),
                            "received_value": str(recv_val),
                            "gap": str(inv.amount - recv_val),
                            "partial_receipt_pending": len(pending) > 0,
                            "pending_lines": pending,
                        },
                        next_actions=["wait_for_receipt", "reduce_invoice_amount", "split_invoice"],
                        workflow_id=po.workflow_id)

    inv.status = InvoiceStatus.MATCHED
    log_event(db, action="match_invoice", entity_type="invoice", entity_id=inv.id,
              workflow_id=po.workflow_id,
              detail={"amount": str(inv.amount), "received_value": str(recv_val),
                       "partial_pending": len(pending) > 0})
    db.commit()
    db.refresh(inv)

    resp = InvoiceRead.model_validate(inv)
    if pending:
        resp.next_actions = ["partial_receipt_pending"]
    return resp


# ── Approve + GL posting ─────────────────────────────────────────

@router.post("/{invoice_id}/approve", response_model=InvoiceRead)
def approve_invoice(invoice_id: int, db: DB):
    """Approve a matched invoice and post GL entries."""
    inv = _get_invoice_or_404(db, invoice_id)

    if inv.status == InvoiceStatus.APPROVED:
        return inv  # idempotent

    if inv.status != InvoiceStatus.MATCHED:
        raise P2PError(409, "INVOICE_NOT_MATCHED",
                        f"Invoice {invoice_id} is in {inv.status.value} status. "
                        "Only MATCHED invoices can be approved.",
                        context={"invoice_id": invoice_id, "current_status": inv.status.value},
                        next_actions=["run_match_first"],
                        workflow_id=inv.purchase_order.workflow_id)

    vendor = db.get(Vendor, inv.vendor_id)

    debit_entry = GLEntry(
        invoice_id=inv.id,
        account_code=AP_CONTROL_ACCOUNT,
        debit=inv.amount,
        credit=Decimal("0.00"),
    )
    credit_entry = GLEntry(
        invoice_id=inv.id,
        account_code=vendor.expense_account_code,
        debit=Decimal("0.00"),
        credit=inv.amount,
    )
    db.add_all([debit_entry, credit_entry])

    assert debit_entry.debit == credit_entry.credit, "GL entries must balance"

    inv.status = InvoiceStatus.APPROVED
    log_event(db, action="approve_invoice", entity_type="invoice", entity_id=inv.id,
              workflow_id=inv.purchase_order.workflow_id,
              detail={"amount": str(inv.amount),
                       "debit_account": AP_CONTROL_ACCOUNT,
                       "credit_account": vendor.expense_account_code})
    db.commit()
    db.refresh(inv)
    return inv
