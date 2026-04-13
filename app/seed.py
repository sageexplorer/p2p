"""Seed data for the demo.

Idempotent: only inserts when the table is empty, so app restarts don't
duplicate rows. Includes one inactive vendor on purpose so we can
demonstrate the "inactive vendor cannot have new POs" validation.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from .enums import PaymentTerms
from .models import Product, Vendor

VENDORS_SEED = [
    {
        "name": "ACME Building Supply",
        "payment_terms": PaymentTerms.NET30,
        "is_active": True,
        "expense_account_code": "5010",  # Materials - Lumber
        "credit_limit": Decimal("50000.00"),
    },
    {
        "name": "Ironclad Hardware Co",
        "payment_terms": PaymentTerms.NET60,
        "is_active": True,
        "expense_account_code": "5020",  # Materials - Hardware
        "credit_limit": Decimal("25000.00"),
    },
    {
        "name": "Legacy Stone & Tile",
        "payment_terms": PaymentTerms.NET30,
        "is_active": False,  # inactive on purpose to demo validation
        "expense_account_code": "5030",
        "credit_limit": Decimal("10000.00"),
    },
]

PRODUCTS_SEED = [
    ("SKU-1001", "2x4x8 Pine Stud", Decimal("4.25")),
    ("SKU-1002", "1/2in Drywall Sheet 4x8", Decimal("12.00")),
    ("SKU-1003", "5lb Box Drywall Screws", Decimal("18.50")),
    ("SKU-1004", "Galvanized Roofing Nails 50ct", Decimal("8.75")),
    ("SKU-2001", "Concrete Mix 80lb Bag", Decimal("6.50")),
    ("SKU-2002", "Rebar #4 20ft", Decimal("9.20")),
    ("SKU-3001", "PVC Pipe 1in 10ft", Decimal("7.40")),
    ("SKU-3002", "Copper Fitting 1/2in Elbow", Decimal("2.10")),
]


def seed_database(db: Session) -> None:
    """Insert seed data if tables are empty. Safe to call on every startup."""
    if db.query(Vendor).count() == 0:
        for v in VENDORS_SEED:
            db.add(Vendor(**v))
    if db.query(Product).count() == 0:
        for sku, desc, cost in PRODUCTS_SEED:
            db.add(Product(sku=sku, description=desc, standard_cost=cost))
    db.commit()
