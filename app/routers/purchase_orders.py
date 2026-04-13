"""Purchase Order endpoints.

Covers the full PO lifecycle: create draft, submit, receive goods.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..audit import log_event
from ..database import get_db
from ..enums import POStatus
from ..errors import P2PError
from ..models import (
    GoodsReceipt,
    GoodsReceiptLineItem,
    POLineItem,
    PurchaseOrder,
    Vendor,
)
from ..schemas import POCreate, PORead, ReceiptCreate, ReceiptRead

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])

DB = Annotated[Session, Depends(get_db)]


def _get_po_or_404(db: Session, po_id: int) -> PurchaseOrder:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise P2PError(404, "PO_NOT_FOUND", f"Purchase order {po_id} not found.",
                        context={"po_id": po_id}, next_actions=["list_purchase_orders"])
    return po


# ── Create draft PO ──────────────────────────────────────────────

@router.post("", response_model=PORead, status_code=201)
def create_purchase_order(body: POCreate, db: DB):
    """Create a new purchase order in DRAFT status."""
    vendor = db.get(Vendor, body.vendor_id)
    if not vendor:
        raise P2PError(404, "VENDOR_NOT_FOUND", f"Vendor {body.vendor_id} not found.",
                        context={"vendor_id": body.vendor_id}, next_actions=["list_vendors"])
    if not vendor.is_active:
        raise P2PError(400, "INACTIVE_VENDOR",
                        f"Vendor {body.vendor_id} is inactive and cannot have new purchase orders.",
                        context={"vendor_id": vendor.id, "vendor_name": vendor.name},
                        next_actions=["use_different_vendor"])

    po = PurchaseOrder(vendor_id=body.vendor_id)
    for li in body.line_items:
        po.line_items.append(POLineItem(
            sku=li.sku,
            description=li.description,
            qty_ordered=li.qty_ordered,
            unit_cost=li.unit_cost,
        ))
    db.add(po)
    db.flush()
    log_event(db, action="create_po", entity_type="po", entity_id=po.id,
              workflow_id=po.workflow_id,
              detail={"vendor_id": po.vendor_id, "lines": len(po.line_items)})
    db.commit()
    db.refresh(po)
    return po


# ── Get PO detail ────────────────────────────────────────────────

@router.get("/{po_id}", response_model=PORead)
def get_purchase_order(po_id: int, db: DB):
    """Retrieve a purchase order with all line items and receipt status."""
    return _get_po_or_404(db, po_id)


# ── Submit PO ────────────────────────────────────────────────────

@router.post("/{po_id}/submit", response_model=PORead)
def submit_purchase_order(po_id: int, db: DB):
    """Transition a PO from DRAFT to SUBMITTED. Idempotent if already SUBMITTED."""
    po = _get_po_or_404(db, po_id)

    if po.status == POStatus.SUBMITTED:
        return po  # idempotent

    if po.status != POStatus.DRAFT:
        raise P2PError(409, "INVALID_STATUS_TRANSITION",
                        f"Cannot submit a PO in {po.status.value} status.",
                        context={"current_status": po.status.value, "attempted_action": "submit"},
                        next_actions=[],
                        workflow_id=po.workflow_id)

    po.status = POStatus.SUBMITTED
    log_event(db, action="submit_po", entity_type="po", entity_id=po.id,
              workflow_id=po.workflow_id, detail={"status": "SUBMITTED"})
    db.commit()
    db.refresh(po)
    return po


# ── Receive goods ────────────────────────────────────────────────

@router.post("/{po_id}/receive", response_model=ReceiptRead, status_code=201)
def receive_goods(po_id: int, body: ReceiptCreate, db: DB):
    """Record a goods receipt against a submitted PO."""
    po = _get_po_or_404(db, po_id)

    if po.status not in (POStatus.SUBMITTED, POStatus.RECEIVED):
        raise P2PError(409, "INVALID_STATUS_TRANSITION",
                        f"Cannot receive goods for a PO in {po.status.value} status.",
                        context={"current_status": po.status.value, "attempted_action": "receive"},
                        next_actions=[],
                        workflow_id=po.workflow_id)

    receipt = GoodsReceipt(po_id=po.id, received_by=body.received_by)

    for li in body.line_items:
        po_line = db.get(POLineItem, li.po_line_item_id)
        if not po_line or po_line.po_id != po.id:
            raise P2PError(404, "PO_LINE_NOT_FOUND",
                            f"PO line item {li.po_line_item_id} not found on PO {po_id}.",
                            context={"po_line_item_id": li.po_line_item_id, "po_id": po_id},
                            next_actions=["check_po_line"])

        max_receivable = po_line.qty_ordered - po_line.qty_received
        if li.qty_received > max_receivable:
            raise P2PError(422, "OVER_RECEIPT",
                            f"Receiving {li.qty_received} units for PO line {li.po_line_item_id} would exceed ordered qty.",
                            context={
                                "po_line_item_id": li.po_line_item_id,
                                "qty_ordered": po_line.qty_ordered,
                                "qty_already_received": po_line.qty_received,
                                "qty_attempted": li.qty_received,
                                "max_receivable": max_receivable,
                            },
                            next_actions=["reduce_qty", "check_po_line"],
                            workflow_id=po.workflow_id)

        po_line.qty_received += li.qty_received
        receipt.line_items.append(GoodsReceiptLineItem(
            po_line_item_id=li.po_line_item_id,
            qty_received=li.qty_received,
        ))

    db.add(receipt)

    all_received = all(
        li.qty_received >= li.qty_ordered for li in po.line_items
    )
    if all_received:
        po.status = POStatus.RECEIVED

    db.flush()
    log_event(db, action="receive_goods", entity_type="po", entity_id=po.id,
              workflow_id=po.workflow_id,
              detail={"receipt_id": receipt.id, "fully_received": all_received})
    db.commit()
    db.refresh(receipt)
    return receipt
