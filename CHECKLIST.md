# P2P API — Implementation Checklist

## Foundation

- [x] Database setup (`app/database.py` — engine, Base, SessionLocal, get_db)
- [x] Enums (`app/enums.py` — PaymentTerms, POStatus, InvoiceStatus)
- [x] ORM models (`app/models.py` — Vendor, Product, PurchaseOrder, POLineItem, GoodsReceipt, GoodsReceiptLineItem, Invoice, GLEntry)
- [x] Seed data (`app/seed.py` — 3 vendors, 8 products, idempotent)
- [x] App entrypoint (`app/main.py` — lifespan, create_all, health check)
- [x] Pydantic schemas (`app/schemas.py`)
- [x] Structured error envelope (`app/errors.py` — P2PError + exception handler)
- [x] Register exception handler in `main.py`

## Routers

- [x] `app/routers/__init__.py`
- [x] `app/routers/vendors.py` — mounted in main.py
- [x] `app/routers/purchase_orders.py` — mounted in main.py
- [x] `app/routers/invoices.py` — mounted in main.py

## Vendor Endpoints

- [x] `GET /vendors` — list all vendors
- [x] `GET /vendors/{id}` — get vendor detail
- [x] `GET /vendors/{id}/exposure` — AP exposure (credit limit, outstanding AP, open invoices)

## Purchase Order Endpoints

- [x] `POST /purchase-orders` — create draft PO (validate vendor active)
- [x] `GET /purchase-orders/{id}` — get PO detail with line items + receipt status
- [x] `POST /purchase-orders/{id}/submit` — DRAFT -> SUBMITTED (idempotent if already SUBMITTED)
- [x] `POST /purchase-orders/{id}/receive` — record goods receipt, update qty_received

## PO Validation Rules

- [x] Inactive vendor -> `INACTIVE_VENDOR` (400)
- [x] Over-receipt -> `OVER_RECEIPT` (422)
- [x] Invalid status transition -> `INVALID_STATUS_TRANSITION` (409)
- [x] Idempotent submit (re-submit returns 200, not 409)
- [x] Auto-transition to RECEIVED when all lines fully received

## Invoice Endpoints

- [x] `POST /invoices` — create invoice against a PO
- [x] `GET /invoices/{id}` — get invoice detail
- [x] `POST /invoices/{id}/match` — 3-way match (invoice amount <= received value)
- [x] `POST /invoices/{id}/approve` — approve + post GL entries

## Invoice Validation Rules

- [x] Amount exceeds received value -> `AMOUNT_EXCEEDS_RECEIVED` (422)
- [x] Partial receipt flagged in next_actions but match still succeeds
- [x] Only MATCHED invoices can be approved -> `INVOICE_NOT_MATCHED` (409)
- [x] Idempotent match (re-match returns 200)
- [x] Idempotent approve (re-approve returns 200, no duplicate GL rows)

## GL Posting

- [x] Debit AP Control (account 2000) for invoice amount
- [x] Credit vendor expense account for invoice amount
- [x] GL debits == GL credits (assert in code)
- [x] GL entries included in approve response

## Testing Smoke Tests (from TESTING.md)

- [x] 0 — Health check
- [x] 1a — Create draft PO (happy path)
- [x] 1b — Create PO against inactive vendor (must fail)
- [x] 1c — Submit PO
- [x] 1d — Submit again (idempotency)
- [x] 1e — Get PO detail
- [x] 1f — Receive goods (full receipt)
- [x] 1g — Receive goods (partial receipt)
- [x] 1h — Over-receive (must fail)
- [x] 1i — Submit on wrong status (must fail)
- [x] 2a — Create invoice
- [x] 2b — 3-way match (happy path)
- [x] 2c — Match invoice exceeds received (must fail)
- [x] 2d — Match with partial receipt pending (succeeds with flag)
- [x] 2e — Match already-matched invoice (idempotency)
- [x] 3a — Approve matched invoice (GL posted)
- [x] 3b — Approve unmatched invoice (must fail)
- [x] 3c — Approve already-approved invoice (idempotency)
- [x] 4a — Vendor exposure with approved invoices
- [x] 4b — Vendor exposure with no invoices
- [x] 5 — Full end-to-end smoke test
