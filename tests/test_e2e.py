"""Full end-to-end smoke test — exercises the entire P2P cycle in sequence."""


def test_full_p2p_cycle(client):
    """Test 5: create PO -> submit -> receive -> invoice -> match -> approve -> exposure."""
    # 1. Create draft PO
    resp = client.post("/purchase-orders", json={
        "vendor_id": 1,
        "line_items": [
            {"sku": "SKU-2001", "description": "Concrete Mix 80lb Bag",
             "qty_ordered": 40, "unit_cost": "6.50"},
        ],
    })
    assert resp.status_code == 201
    po = resp.json()
    assert po["status"] == "DRAFT"

    # 2. Submit PO
    resp = client.post(f"/purchase-orders/{po['id']}/submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUBMITTED"

    # 3. Receive all goods
    line = po["line_items"][0]
    resp = client.post(f"/purchase-orders/{po['id']}/receive", json={
        "received_by": "Floor Manager",
        "line_items": [{"po_line_item_id": line["id"], "qty_received": 40}],
    })
    assert resp.status_code == 201

    # PO should be RECEIVED
    resp = client.get(f"/purchase-orders/{po['id']}")
    assert resp.json()["status"] == "RECEIVED"

    # 4. Create invoice (40 x $6.50 = $260.00)
    resp = client.post("/invoices", json={
        "vendor_id": 1,
        "po_id": po["id"],
        "invoice_number": "INV-2024-010",
        "amount": "260.00",
    })
    assert resp.status_code == 201
    inv = resp.json()
    assert inv["status"] == "PENDING"

    # 5. 3-way match
    resp = client.post(f"/invoices/{inv['id']}/match")
    assert resp.status_code == 200
    assert resp.json()["status"] == "MATCHED"

    # 6. Approve and post GL
    resp = client.post(f"/invoices/{inv['id']}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert len(data["gl_entries"]) == 2
    total_debit = sum(float(e["debit"]) for e in data["gl_entries"])
    total_credit = sum(float(e["credit"]) for e in data["gl_entries"])
    assert total_debit == total_credit == 260.00

    # 7. Check vendor exposure
    resp = client.get("/vendors/1/exposure")
    assert resp.status_code == 200
    exposure = resp.json()
    assert float(exposure["total_outstanding_ap"]) == 260.00
    assert exposure["open_invoices"] >= 1


def test_list_vendors(client):
    resp = client.get("/vendors")
    assert resp.status_code == 200
    vendors = resp.json()
    print(vendors)
    assert len(vendors) == 3


def test_get_vendor(client):
    resp = client.get("/vendors/1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "ACME Building Supply"


def test_get_vendor_not_found(client):
    resp = client.get("/vendors/9999")
    assert resp.status_code == 404
