"""Vendor endpoints.

Includes AP exposure calculation for credit-limit awareness.
"""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..enums import InvoiceStatus
from ..errors import P2PError
from ..models import Invoice, Vendor
from ..schemas import VendorExposure, VendorRead

router = APIRouter(prefix="/vendors", tags=["vendors"])

DB = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[VendorRead])
def list_vendors(db: DB):
    """List all vendors."""
    return db.query(Vendor).all()


@router.get("/{vendor_id}", response_model=VendorRead)
def get_vendor(vendor_id: int, db: DB):
    """Get a single vendor by ID."""
    vendor = db.get(Vendor, vendor_id)
    if not vendor:
        raise P2PError(404, "VENDOR_NOT_FOUND",
                        f"Vendor {vendor_id} not found.",
                        context={"vendor_id": vendor_id},
                        next_actions=["list_vendors"])
    return vendor


@router.get("/{vendor_id}/exposure", response_model=VendorExposure)
def get_vendor_exposure(vendor_id: int, db: DB):
    """Calculate AP exposure: outstanding approved/matched invoices vs credit limit."""
    vendor = db.get(Vendor, vendor_id)
    if not vendor:
        raise P2PError(404, "VENDOR_NOT_FOUND",
                        f"Vendor {vendor_id} not found.",
                        context={"vendor_id": vendor_id},
                        next_actions=["list_vendors"])

    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.vendor_id == vendor_id,
            Invoice.status.in_([InvoiceStatus.MATCHED, InvoiceStatus.APPROVED]),
        )
        .all()
    )

    total_ap = sum(inv.amount for inv in invoices) if invoices else Decimal("0.00")
    credit_remaining = (vendor.credit_limit - total_ap) if vendor.credit_limit else None

    return VendorExposure(
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        total_outstanding_ap=total_ap,
        credit_limit=vendor.credit_limit,
        credit_remaining=credit_remaining,
        open_invoices=len(invoices),
    )
