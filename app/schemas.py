"""Pydantic v2 request/response models for the P2P API."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .enums import InvoiceStatus, POStatus, PaymentTerms

# Decimal fields always serialize to 2 decimal places (e.g. "1350.00")
Money = Annotated[Decimal, Field(decimal_places=2, max_digits=12)]


# ── Purchase Orders ──────────────────────────────────────────────


class POLineItemCreate(BaseModel):
    sku: str
    description: str
    qty_ordered: int
    unit_cost: Money


class POCreate(BaseModel):
    vendor_id: int
    line_items: list[POLineItemCreate]


class POLineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    description: str
    qty_ordered: int
    qty_received: int
    unit_cost: Money


class PORead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    status: POStatus
    workflow_id: str
    created_at: datetime
    line_items: list[POLineItemRead]


# ── Goods Receipt ────────────────────────────────────────────────


class ReceiptLineItemCreate(BaseModel):
    po_line_item_id: int
    qty_received: int


class ReceiptCreate(BaseModel):
    received_by: str
    line_items: list[ReceiptLineItemCreate]


class ReceiptLineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    po_line_item_id: int
    qty_received: int


class ReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    po_id: int
    received_by: str
    received_at: datetime
    line_items: list[ReceiptLineItemRead]


# ── Invoices ─────────────────────────────────────────────────────


class InvoiceCreate(BaseModel):
    vendor_id: int
    po_id: int
    invoice_number: str
    amount: Money


class GLEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_code: str
    debit: Money
    credit: Money


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    po_id: int
    invoice_number: str
    amount: Money
    status: InvoiceStatus
    created_at: datetime
    gl_entries: list[GLEntryRead] = []
    next_actions: list[str] = []


# ── Vendors ──────────────────────────────────────────────────────


class VendorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    payment_terms: PaymentTerms
    is_active: bool
    expense_account_code: str
    credit_limit: Optional[Money]


class VendorExposure(BaseModel):
    vendor_id: int
    vendor_name: str
    total_outstanding_ap: Money
    credit_limit: Optional[Money]
    credit_remaining: Optional[Money]
    open_invoices: int
