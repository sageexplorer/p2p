"""E2E-01: Full P2P cycle — create → submit → receive → invoice → match → approve → exposure.

Pre-conditions: Fresh database with seed data. No prior POs.

This test exercises every step of the procurement workflow in sequence,
verifying each state transition, field value, and side effect along the way.
"""


def test_e2e_01_full_p2p_cycle(client):
    # ──────────────────────────────────────────────────────────────
    # Step 1: Create a draft PO for vendor 1
    # ──────────────────────────────────────────────────────────────
    resp = client.post("/purchase-orders", json={
        "vendor_id": 1,
        "line_items": [
            {"sku": "SKU-2001", "description": "Concrete Mix 80lb Bag",
             "qty_ordered": 40, "unit_cost": "6.50"},
        ],
    })

    # Verify: 201 Created
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    po = resp.json()

    # Verify: status is DRAFT
    assert po["status"] == "DRAFT"

    # Verify: vendor_id matches what we sent
    assert po["vendor_id"] == 1

    # Verify: workflow_id is a UUID (36 chars with dashes: 8-4-4-4-12)
    assert len(po["workflow_id"]) == 36
    assert po["workflow_id"].count("-") == 4

    # Verify: exactly 1 line item returned
    assert len(po["line_items"]) == 1

    # Verify: line item fields match input
    line = po["line_items"][0]
    assert line["sku"] == "SKU-2001"
    assert line["description"] == "Concrete Mix 80lb Bag"
    assert line["qty_ordered"] == 40
    assert line["unit_cost"] == "6.50"

    # Verify: qty_received starts at 0
    assert line["qty_received"] == 0

    # Verify: id is a positive integer
    assert isinstance(po["id"], int) and po["id"] > 0
    assert isinstance(line["id"], int) and line["id"] > 0

    # Verify: created_at is present and non-empty
    assert po["created_at"] is not None and len(po["created_at"]) > 0

    po_id = po["id"]
    line_id = line["id"]
    workflow_id = po["workflow_id"]

    # ──────────────────────────────────────────────────────────────
    # Step 2: Submit the PO
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/purchase-orders/{po_id}/submit")

    # Verify: 200 OK
    assert resp.status_code == 200
    submitted = resp.json()

    # Verify: status transitioned to SUBMITTED
    assert submitted["status"] == "SUBMITTED"

    # Verify: all other fields unchanged
    assert submitted["id"] == po_id
    assert submitted["vendor_id"] == 1
    assert submitted["workflow_id"] == workflow_id
    assert len(submitted["line_items"]) == 1

    # ──────────────────────────────────────────────────────────────
    # Step 3: Confirm PO is SUBMITTED via GET
    # ──────────────────────────────────────────────────────────────
    resp = client.get(f"/purchase-orders/{po_id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "SUBMITTED"

    # Verify: qty_received still 0 (no receipt yet)
    assert resp.json()["line_items"][0]["qty_received"] == 0

    # ──────────────────────────────────────────────────────────────
    # Step 4: Receive all goods (40 units)
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/purchase-orders/{po_id}/receive", json={
        "received_by": "Floor Manager",
        "line_items": [
            {"po_line_item_id": line_id, "qty_received": 40},
        ],
    })

    # Verify: 201 Created (new receipt record)
    assert resp.status_code == 201
    receipt = resp.json()

    # Verify: receipt fields
    assert receipt["po_id"] == po_id
    assert receipt["received_by"] == "Floor Manager"
    assert receipt["received_at"] is not None
    assert isinstance(receipt["id"], int) and receipt["id"] > 0

    # Verify: receipt line items
    assert len(receipt["line_items"]) == 1
    assert receipt["line_items"][0]["po_line_item_id"] == line_id
    assert receipt["line_items"][0]["qty_received"] == 40

    # ──────────────────────────────────────────────────────────────
    # Step 5: Confirm PO is now RECEIVED via GET
    # ──────────────────────────────────────────────────────────────
    resp = client.get(f"/purchase-orders/{po_id}")

    assert resp.status_code == 200
    po_after = resp.json()

    # Verify: status auto-transitioned to RECEIVED (all lines fully received)
    assert po_after["status"] == "RECEIVED"

    # Verify: qty_received now equals qty_ordered
    assert po_after["line_items"][0]["qty_received"] == 40
    assert po_after["line_items"][0]["qty_ordered"] == 40

    # ──────────────────────────────────────────────────────────────
    # Step 6: Create invoice (40 x $6.50 = $260.00)
    # ──────────────────────────────────────────────────────────────
    resp = client.post("/invoices", json={
        "vendor_id": 1,
        "po_id": po_id,
        "invoice_number": "INV-E2E-001",
        "amount": "260.00",
    })

    # Verify: 201 Created
    assert resp.status_code == 201
    invoice = resp.json()

    # Verify: invoice fields
    assert invoice["status"] == "PENDING"
    assert invoice["vendor_id"] == 1
    assert invoice["po_id"] == po_id
    assert invoice["invoice_number"] == "INV-E2E-001"
    assert invoice["amount"] == "260.00"

    # Verify: no GL entries yet (not approved)
    assert invoice["gl_entries"] == []

    # Verify: id is a positive integer
    assert isinstance(invoice["id"], int) and invoice["id"] > 0

    inv_id = invoice["id"]

    # ──────────────────────────────────────────────────────────────
    # Step 7: 3-way match
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/invoices/{inv_id}/match")

    # Verify: 200 OK (match succeeds)
    assert resp.status_code == 200
    matched = resp.json()

    # Verify: status transitioned to MATCHED
    assert matched["status"] == "MATCHED"

    # Verify: no partial_receipt_pending flag (all goods received)
    assert "partial_receipt_pending" not in matched.get("next_actions", [])

    # Verify: still no GL entries (not approved yet)
    assert matched["gl_entries"] == []

    # ──────────────────────────────────────────────────────────────
    # Step 8: Approve and post GL entries
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/invoices/{inv_id}/approve")

    # Verify: 200 OK
    assert resp.status_code == 200
    approved = resp.json()

    # Verify: status transitioned to APPROVED
    assert approved["status"] == "APPROVED"

    # Verify: exactly 2 GL entries created
    gl = approved["gl_entries"]
    assert len(gl) == 2, f"Expected 2 GL entries, got {len(gl)}"

    # Verify: GL entry 1 — Debit AP Control (account 2000)
    debit_entry = next(e for e in gl if e["account_code"] == "2000")
    assert debit_entry["debit"] == "260.00"
    assert debit_entry["credit"] == "0.00"

    # Verify: GL entry 2 — Credit vendor expense (account 5010 for ACME)
    credit_entry = next(e for e in gl if e["account_code"] == "5010")
    assert credit_entry["debit"] == "0.00"
    assert credit_entry["credit"] == "260.00"

    # Verify: GL balances — sum(debits) == sum(credits)
    total_debit = sum(float(e["debit"]) for e in gl)
    total_credit = sum(float(e["credit"]) for e in gl)
    assert total_debit == total_credit == 260.00

    # Verify: each GL entry has a positive id
    for entry in gl:
        assert isinstance(entry["id"], int) and entry["id"] > 0

    # ──────────────────────────────────────────────────────────────
    # Step 9: Check vendor exposure
    # ──────────────────────────────────────────────────────────────
    resp = client.get("/vendors/1/exposure")

    # Verify: 200 OK
    assert resp.status_code == 200
    exposure = resp.json()

    # Verify: vendor info
    assert exposure["vendor_id"] == 1
    assert exposure["vendor_name"] == "ACME Building Supply"

    # Verify: total_outstanding_ap includes our $260 invoice
    outstanding = float(exposure["total_outstanding_ap"])
    assert outstanding >= 260.00, f"Expected AP >= 260.00, got {outstanding}"

    # Verify: credit limit
    assert exposure["credit_limit"] == "50000.00"

    # Verify: credit remaining = limit - outstanding
    remaining = float(exposure["credit_remaining"])
    expected_remaining = 50000.00 - outstanding
    assert abs(remaining - expected_remaining) < 0.01, \
        f"credit_remaining={remaining}, expected={expected_remaining}"

    # Verify: at least 1 open invoice
    assert exposure["open_invoices"] >= 1

    # ──────────────────────────────────────────────────────────────
    # Step 10: Verify audit trail for this workflow
    # ──────────────────────────────────────────────────────────────
    resp = client.get(f"/api/audit-logs?workflow_id={workflow_id}")

    assert resp.status_code == 200
    logs = resp.json()

    # Verify: 6 audit events for the full lifecycle
    actions = [log["action"] for log in logs]
    assert "create_po" in actions, "Missing create_po audit event"
    assert "submit_po" in actions, "Missing submit_po audit event"
    assert "receive_goods" in actions, "Missing receive_goods audit event"
    assert "create_invoice" in actions, "Missing create_invoice audit event"
    assert "match_invoice" in actions, "Missing match_invoice audit event"
    assert "approve_invoice" in actions, "Missing approve_invoice audit event"

    # Verify: all events share the same workflow_id
    for log in logs:
        assert log["workflow_id"] == workflow_id

    # Verify: each event has a timestamp
    for log in logs:
        assert log["timestamp"] is not None

    # ──────────────────────────────────────────────────────────────
    # Step 11: Verify idempotency — re-approve should not duplicate GL
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/invoices/{inv_id}/approve")

    assert resp.status_code == 200
    re_approved = resp.json()

    # Verify: still exactly 2 GL entries (not 4)
    assert len(re_approved["gl_entries"]) == 2

    # Verify: same GL entry IDs as before
    original_ids = sorted(e["id"] for e in gl)
    re_approve_ids = sorted(e["id"] for e in re_approved["gl_entries"])
    assert original_ids == re_approve_ids, "Re-approve created duplicate GL entries!"

    # ──────────────────────────────────────────────────────────────
    # Step 12: Verify idempotency — re-match returns current state
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/invoices/{inv_id}/match")

    assert resp.status_code == 200
    assert resp.json()["status"] == "APPROVED"  # returns current state, not MATCHED

    # ──────────────────────────────────────────────────────────────
    # Step 13: Verify idempotency — re-submit a RECEIVED PO fails
    # ──────────────────────────────────────────────────────────────
    resp = client.post(f"/purchase-orders/{po_id}/submit")

    assert resp.status_code == 409
    err = resp.json()
    assert err["error_code"] == "INVALID_STATUS_TRANSITION"
    assert err["context"]["current_status"] == "RECEIVED"
