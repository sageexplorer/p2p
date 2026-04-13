"""Domain enums.

Subclassing `str` so they serialize cleanly to JSON and so the agent
sees stable string values like "DRAFT" rather than integer codes.
"""
import enum


class PaymentTerms(str, enum.Enum):
    NET30 = "NET30"
    NET60 = "NET60"


class POStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"


class InvoiceStatus(str, enum.Enum):
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    APPROVED = "APPROVED"
    PAID = "PAID"
