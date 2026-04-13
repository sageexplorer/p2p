"""SQLAlchemy ORM models for the P2P domain.

Design notes (decisions from the interview cheat sheet):
- Money is `Numeric(12, 2)` and surfaces as `Decimal` — never float.
- `PurchaseOrder.workflow_id` is a UUID threaded through the procurement
  cycle so logs/traces can correlate PO -> Receipt -> Invoice -> GL.
- Multi-receipt is supported: a PO can have many `GoodsReceipt` rows,
  each with its own line items pointing back at PO line items.
- `Vendor.expense_account_code` lives on the vendor (per cheat-sheet
  decision) so GL credit posting is a single lookup.
- `POLineItem.qty_received` is denormalized for fast access; the source of
  truth is the sum of `GoodsReceiptLineItem.qty_received` for that line.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .enums import InvoiceStatus, POStatus, PaymentTerms


def _utcnow() -> datetime:
    """Timezone-aware UTC now (datetime.utcnow is deprecated in 3.12)."""
    return datetime.now(timezone.utc)


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    payment_terms: Mapped[PaymentTerms] = mapped_column(SAEnum(PaymentTerms))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # GL credit account used when an invoice from this vendor is approved.
    expense_account_code: Mapped[str] = mapped_column(String(20))
    # For the stretch credit-limit check.
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    purchase_orders: Mapped[List["PurchaseOrder"]] = relationship(
        back_populates="vendor"
    )
    invoices: Mapped[List["Invoice"]] = relationship(back_populates="vendor")


class Product(Base):
    """Catalog of known SKUs.

    Not in the README's entity list, but we need somewhere for "seed 5-10
    SKUs" to live and it lets endpoints validate SKUs exist before quoting
    them on a PO. Trivial cost, real value.
    """
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(String(200))
    standard_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    status: Mapped[POStatus] = mapped_column(
        SAEnum(POStatus), default=POStatus.DRAFT
    )
    workflow_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    vendor: Mapped["Vendor"] = relationship(back_populates="purchase_orders")
    line_items: Mapped[List["POLineItem"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )
    receipts: Mapped[List["GoodsReceipt"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )
    invoices: Mapped[List["Invoice"]] = relationship(
        back_populates="purchase_order"
    )


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    sku: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(200))
    qty_ordered: Mapped[int] = mapped_column(Integer)
    # Denormalized convenience: sum of receipt line qtys for this PO line.
    qty_received: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    purchase_order: Mapped["PurchaseOrder"] = relationship(
        back_populates="line_items"
    )


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    received_by: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    purchase_order: Mapped["PurchaseOrder"] = relationship(
        back_populates="receipts"
    )
    line_items: Mapped[List["GoodsReceiptLineItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class GoodsReceiptLineItem(Base):
    __tablename__ = "goods_receipt_line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id"))
    po_line_item_id: Mapped[int] = mapped_column(
        ForeignKey("po_line_items.id")
    )
    qty_received: Mapped[int] = mapped_column(Integer)

    receipt: Mapped["GoodsReceipt"] = relationship(back_populates="line_items")
    po_line_item: Mapped["POLineItem"] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    invoice_number: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus), default=InvoiceStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    vendor: Mapped["Vendor"] = relationship(back_populates="invoices")
    purchase_order: Mapped["PurchaseOrder"] = relationship(
        back_populates="invoices"
    )
    gl_entries: Mapped[List["GLEntry"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class GLEntry(Base):
    __tablename__ = "gl_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    account_code: Mapped[str] = mapped_column(String(20))
    debit: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )
    credit: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    invoice: Mapped["Invoice"] = relationship(back_populates="gl_entries")


class AuditLog(Base):
    """Persisted audit trail for observability dashboard."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    workflow_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
