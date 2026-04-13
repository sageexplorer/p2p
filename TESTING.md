# P2P API — Testing Playbook

Run the app first:
```bash
.venv/bin/uvicorn app.main:app --reload
```

Base URL: `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs`

All examples use `curl`. Replace with Postman / httpie / Thunder Client — whatever you prefer.

---

## 0. Health check

```bash
curl http://localhost:8000/health
```
Expected:
```json
{"status": "ok"}
```

---

## 1. PO Lifecycle

### 1a. Create a draft PO (happy path)

```bash
curl -X POST http://localhost:8000/purchase-orders \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": 1,
    "line_items": [
      {"sku": "SKU-1001", "description": "2x4x8 Pine Stud", "qty_ordered": 100, "unit_cost": "4.25"},
      {"sku": "SKU-1003", "description": "5lb Box Drywall Screws", "qty_ordered": 50, "unit_cost": "18.50"}
    ]
  }'
```
Expected: `201 Created`
- `status` = `"DRAFT"`
- `vendor_id` = `1`
- `workflow_id` is a UUID
- Two line items with `qty_received` = `0`

### 1b. Create PO against inactive vendor (must fail)

Vendor 3 ("Legacy Stone & Tile") is inactive.

```bash
curl -X POST http://localhost:8000/purchase-orders \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": 3,
    "line_items": [
      {"sku": "SKU-2001", "description": "Concrete Mix 80lb Bag", "qty_ordered": 20, "unit_cost": "6.50"}
    ]
  }'
```
Expected: `400 Bad Request`
```json
{
  "error_code": "INACTIVE_VENDOR",
  "message": "Vendor 3 is inactive and cannot have new purchase orders.",
  "context": {"vendor_id": 3, "vendor_name": "Legacy Stone & Tile"},
  "next_actions": ["use_different_vendor"]
}
```

### 1c. Submit a PO

```bash
curl -X POST http://localhost:8000/purchase-orders/1/submit
```
Expected: `200 OK`
- `status` = `"SUBMITTED"`

### 1d. Submit again (idempotency test)

```bash
curl -X POST http://localhost:8000/purchase-orders/1/submit
```
Expected: `200 OK` — returns current state, does NOT return `409`.

### 1e. Get PO detail

```bash
curl http://localhost:8000/purchase-orders/1
```
Expected: `200 OK`
- Full PO with vendor info, all line items, receipt status per line, workflow_id.

### 1f. Receive goods (full receipt)

```bash
curl -X POST http://localhost:8000/purchase-orders/1/receive \
  -H "Content-Type: application/json" \
  -d '{
    "received_by": "Warehouse Manager",
    "line_items": [
      {"po_line_item_id": 1, "qty_received": 100},
      {"po_line_item_id": 2, "qty_received": 50}
    ]
  }'
```
Expected: `201 Created`
- A `GoodsReceipt` object.
- PO line items now show `qty_received` = `qty_ordered`.
- PO `status` transitions to `"RECEIVED"`.

### 1g. Receive goods (partial receipt)

First, create + submit a new PO:
```bash
curl -X POST http://localhost:8000/purchase-orders \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": 2,
    "line_items": [
      {"sku": "SKU-1004", "description": "Galvanized Roofing Nails 50ct", "qty_ordered": 200, "unit_cost": "8.75"}
    ]
  }'

curl -X POST http://localhost:8000/purchase-orders/2/submit
```
Then receive only half:
```bash
curl -X POST http://localhost:8000/purchase-orders/2/receive \
  -H "Content-Type: application/json" \
  -d '{
    "received_by": "Dock Worker",
    "line_items": [
      {"po_line_item_id": 3, "qty_received": 100}
    ]
  }'
```
Expected: `201 Created`
- `qty_received` = `100`, `qty_ordered` = `200`.
- PO status stays `"SUBMITTED"` (not all items received yet).

### 1h. Over-receive (must fail)

Try to receive 200 more on a line that only has 100 remaining:
```bash
curl -X POST http://localhost:8000/purchase-orders/2/receive \
  -H "Content-Type: application/json" \
  -d '{
    "received_by": "Dock Worker",
    "line_items": [
      {"po_line_item_id": 3, "qty_received": 200}
    ]
  }'
```
Expected: `422 Unprocessable Entity`
```json
{
  "error_code": "OVER_RECEIPT",
  "message": "Receiving 200 units for PO line 3 would exceed ordered qty.",
  "context": {"po_line_item_id": 3, "qty_ordered": 200, "qty_already_received": 100, "qty_attempted": 200, "max_receivable": 100},
  "next_actions": ["reduce_qty", "check_po_line"]
}
```

### 1i. Submit a DRAFT-only operation on wrong status

Try to submit a PO that's already in RECEIVED:
```bash
curl -X POST http://localhost:8000/purchase-orders/1/submit
```
This is the idempotency question: if you chose **idempotent**, it returns 200 with current state. If you chose **strict**, it returns 409. We defaulted to idempotent — but if the PO is in RECEIVED (not SUBMITTED), this should reject because RECEIVED → SUBMITTED is backwards.

Expected: `409 Conflict`
```json
{
  "error_code": "INVALID_STATUS_TRANSITION",
  "message": "Cannot submit a PO in RECEIVED status.",
  "context": {"current_status": "RECEIVED", "attempted_action": "submit"},
  "next_actions": []
}
```

---

## 2. Invoice Matching

### 2a. Create an invoice (happy path)

Against PO 1 (fully received: 100 x $4.25 + 50 x $18.50 = $1350.00):
```bash
curl -X POST http://localhost:8000/invoices \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": 1,
    "po_id": 1,
    "invoice_number": "INV-2024-001",
    "amount": "1350.00"
  }'
```
Expected: `201 Created`
- `status` = `"PENDING"`

### 2b. 3-way match (happy path — amounts tie out, fully received)

```bash
curl -X POST http://localhost:8000/invoices/1/match
```
Expected: `200 OK`
- `status` = `"MATCHED"`
- No errors, because:
  - Invoice amount ($1350) = received value ($1350).
  - All items fully received.

### 2c. 3-way match — invoice exceeds received value (must fail)

Create an invoice that's too high against PO 2 (only 100 of 200 units received, so received value = 100 x $8.75 = $875):
```bash
curl -X POST http://localhost:8000/invoices \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": 2,
    "po_id": 2,
    "invoice_number": "INV-2024-002",
    "amount": "1750.00"
  }'

curl -X POST http://localhost:8000/invoices/2/match
```
Expected: `422 Unprocessable Entity`
```json
{
  "error_code": "AMOUNT_EXCEEDS_RECEIVED",
  "message": "Invoice amount 1750.00 exceeds received goods value 875.00.",
  "context": {
    "invoice_amount": "1750.00",
    "received_value": "875.00",
    "gap": "875.00",
    "partial_receipt_pending": true,
    "pending_lines": [
      {"po_line_item_id": 3, "sku": "SKU-1004", "qty_ordered": 200, "qty_received": 100, "qty_outstanding": 100}
    ]
  },
  "next_actions": ["wait_for_receipt", "reduce_invoice_amount", "split_invoice"]
}
```

### 2d. 3-way match — amount is under received value but partial receipt pending

Create an invoice for $875 against PO 2 (matches received value exactly, but 100 units still outstanding):
```bash
curl -X POST http://localhost:8000/invoices \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": 2,
    "po_id": 2,
    "invoice_number": "INV-2024-003",
    "amount": "875.00"
  }'

curl -X POST http://localhost:8000/invoices/3/match
```
Expected: `200 OK`
- `status` = `"MATCHED"` (amounts tie out).
- Response includes a flag or `next_actions` containing `"partial_receipt_pending"` — the match **succeeds** but the agent is informed that more goods are expected.

### 2e. Match an already-matched invoice (idempotency)

```bash
curl -X POST http://localhost:8000/invoices/1/match
```
Expected: `200 OK` — returns current MATCHED state.

---

## 3. GL Posting

### 3a. Approve a matched invoice (happy path)

```bash
curl -X POST http://localhost:8000/invoices/1/approve
```
Expected: `200 OK`
- Invoice `status` = `"APPROVED"`.
- Response includes `gl_entries`:
```json
{
  "gl_entries": [
    {"account_code": "2000", "debit": "1350.00", "credit": "0.00"},
    {"account_code": "5010", "debit": "0.00", "credit": "1350.00"}
  ]
}
```
Verify: **debit total == credit total** ($1350.00).

Account `2000` = AP Control (hardcoded). Account `5010` = ACME's expense account.

### 3b. Approve an unmatched invoice (must fail)

Invoice 2 was never matched (it failed the 3-way match in test 2c):
```bash
curl -X POST http://localhost:8000/invoices/2/approve
```
Expected: `409 Conflict`
```json
{
  "error_code": "INVOICE_NOT_MATCHED",
  "message": "Invoice 2 is in PENDING status. Only MATCHED invoices can be approved.",
  "context": {"invoice_id": 2, "current_status": "PENDING"},
  "next_actions": ["run_match_first"]
}
```

### 3c. Approve an already-approved invoice (idempotency)

```bash
curl -X POST http://localhost:8000/invoices/1/approve
```
Expected: `200 OK` — returns current APPROVED state with existing GL entries. Does NOT create duplicate GL rows.

---

## 4. Stretch — Vendor Exposure

### 4a. Get AP exposure for vendor with approved invoices

```bash
curl http://localhost:8000/vendors/1/exposure
```
Expected: `200 OK`
```json
{
  "vendor_id": 1,
  "vendor_name": "ACME Building Supply",
  "total_outstanding_ap": "1350.00",
  "credit_limit": "50000.00",
  "credit_remaining": "48650.00",
  "open_invoices": 1
}
```

### 4b. Get exposure for vendor with no invoices

```bash
curl http://localhost:8000/vendors/2/exposure
```
Expected: `200 OK` — `total_outstanding_ap` = `"0.00"` or reflects only MATCHED/APPROVED invoices.

---

## 5. Full happy-path walkthrough (end-to-end smoke test)

Run these in sequence to exercise the entire P2P cycle:

```bash
# 1. Create draft PO
curl -s -X POST http://localhost:8000/purchase-orders \
  -H "Content-Type: application/json" \
  -d '{"vendor_id": 1, "line_items": [{"sku": "SKU-2001", "description": "Concrete Mix 80lb Bag", "qty_ordered": 40, "unit_cost": "6.50"}]}'

# 2. Submit PO (use the ID from step 1)
curl -s -X POST http://localhost:8000/purchase-orders/3/submit

# 3. Receive all goods
curl -s -X POST http://localhost:8000/purchase-orders/3/receive \
  -H "Content-Type: application/json" \
  -d '{"received_by": "Floor Manager", "line_items": [{"po_line_item_id": 4, "qty_received": 40}]}'

# 4. Create invoice (40 x $6.50 = $260.00)
curl -s -X POST http://localhost:8000/invoices \
  -H "Content-Type: application/json" \
  -d '{"vendor_id": 1, "po_id": 3, "invoice_number": "INV-2024-010", "amount": "260.00"}'

# 5. 3-way match
curl -s -X POST http://localhost:8000/invoices/4/match

# 6. Approve and post GL
curl -s -X POST http://localhost:8000/invoices/4/approve

# 7. Check vendor exposure
curl -s http://localhost:8000/vendors/1/exposure
```

Each step should return a clean 200/201. If any step returns a 4xx, something is wrong — the error envelope tells you exactly what.

---

## Checklist — what must never break

| Rule | Test that proves it |
|---|---|
| Inactive vendor cannot create PO | 1b |
| Cannot over-receive | 1h |
| Invoice > received value rejects | 2c |
| Partial receipt flagged but match can succeed | 2d |
| Only MATCHED invoices can be approved | 3b |
| GL debits == GL credits | 3a (verify totals) |
| Idempotent submit | 1d |
| Idempotent match | 2e |
| Idempotent approve | 3c |
| Status never goes backwards | 1i |
