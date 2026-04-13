"""Tests for invoice and GL endpoints.

Like the PO tests, these are written against the spec and will fail
until the routers are implemented.
"""
import pytest


# --------------- helpers ---------------

def _create_fully_received_po(client, vendor_id=1):
    """Create, submit, and fully receive a PO. Returns the PO dict."""
    resp = client.post("/purchase-orders", json={
        "vendor_id": vendor_id,
        "line_items": [
            {"sku": "SKU-1001", "description": "2x4x8 Pine Stud",
             "qty_ordered": 100, "unit_cost": "4.25"},
            {"sku": "SKU-1003", "description": "5lb Box Drywall Screws",
             "qty_ordered": 50, "unit_cost": "18.50"},
        ],
    })
    po = resp.json()
    client.post(f"/purchase-orders/{po['id']}/submit")
    client.post(f"/purchase-orders/{po['id']}/receive", json={
        "received_by": "Tester",
        "line_items": [
            {"po_line_item_id": li["id"], "qty_received": li["qty_ordered"]}
            for li in po["line_items"]
        ],
    })
    return po


def _create_invoice(client, vendor_id, po_id, invoice_number, amount):
    return client.post("/invoices", json={
        "vendor_id": vendor_id,
        "po_id": po_id,
        "invoice_number": invoice_number,
        "amount": amount,
    })


# --------------- 2a: Create invoice ---------------

class TestCreateInvoice:
    def test_happy_path(self, client):
        po = _create_fully_received_po(client)
        resp = _create_invoice(client, 1, po["id"], "INV-001", "1350.00")
        assert resp.status_code == 201
        assert resp.json()["status"] == "PENDING"


# --------------- 2b-e: 3-way match ---------------

class TestMatch:
    def test_match_happy_path(self, client):
        """2b — invoice == received value, fully received."""
        po = _create_fully_received_po(client)
        inv = _create_invoice(client, 1, po["id"], "INV-001", "1350.00").json()
        resp = client.post(f"/invoices/{inv['id']}/match")
        assert resp.status_code == 200
        assert resp.json()["status"] == "MATCHED"

    def test_match_exceeds_received(self, client):
        """2c — invoice amount > received value."""
        # PO with partial receipt
        resp = client.post("/purchase-orders", json={
            "vendor_id": 2,
            "line_items": [
                {"sku": "SKU-1004", "description": "Nails",
                 "qty_ordered": 200, "unit_cost": "8.75"},
            ],
        })
        po = resp.json()
        client.post(f"/purchase-orders/{po['id']}/submit")
        line = po["line_items"][0]
        client.post(f"/purchase-orders/{po['id']}/receive", json={
            "received_by": "Tester",
            "line_items": [{"po_line_item_id": line["id"], "qty_received": 100}],
        })
        # invoice for full amount (1750) but only 875 received
        inv = _create_invoice(client, 2, po["id"], "INV-002", "1750.00").json()
        resp = client.post(f"/invoices/{inv['id']}/match")
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "AMOUNT_EXCEEDS_RECEIVED"

    def test_match_partial_receipt_succeeds_with_flag(self, client):
        """2d — amount <= received value but not all goods in yet."""
        resp = client.post("/purchase-orders", json={
            "vendor_id": 2,
            "line_items": [
                {"sku": "SKU-1004", "description": "Nails",
                 "qty_ordered": 200, "unit_cost": "8.75"},
            ],
        })
        po = resp.json()
        client.post(f"/purchase-orders/{po['id']}/submit")
        line = po["line_items"][0]
        client.post(f"/purchase-orders/{po['id']}/receive", json={
            "received_by": "Tester",
            "line_items": [{"po_line_item_id": line["id"], "qty_received": 100}],
        })
        # invoice for exactly received value (875)
        inv = _create_invoice(client, 2, po["id"], "INV-003", "875.00").json()
        resp = client.post(f"/invoices/{inv['id']}/match")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "MATCHED"
        # should flag partial receipt in next_actions
        assert "partial_receipt_pending" in data.get("next_actions", [])

    def test_match_idempotent(self, client):
        """2e — re-matching a MATCHED invoice returns 200."""
        po = _create_fully_received_po(client)
        inv = _create_invoice(client, 1, po["id"], "INV-001", "1350.00").json()
        client.post(f"/invoices/{inv['id']}/match")
        resp = client.post(f"/invoices/{inv['id']}/match")
        assert resp.status_code == 200
        assert resp.json()["status"] == "MATCHED"


# --------------- 3a-c: Approve + GL ---------------

class TestApprove:
    def test_approve_happy_path(self, client):
        """3a — approve creates balanced GL entries."""
        po = _create_fully_received_po(client)
        inv = _create_invoice(client, 1, po["id"], "INV-001", "1350.00").json()
        client.post(f"/invoices/{inv['id']}/match")
        resp = client.post(f"/invoices/{inv['id']}/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "APPROVED"
        gl = data["gl_entries"]
        assert len(gl) == 2
        total_debit = sum(float(e["debit"]) for e in gl)
        total_credit = sum(float(e["credit"]) for e in gl)
        assert total_debit == total_credit
        # AP Control = 2000, vendor expense = 5010 (ACME)
        codes = {e["account_code"] for e in gl}
        assert "2000" in codes
        assert "5010" in codes

    def test_approve_unmatched_rejected(self, client):
        """3b — cannot approve PENDING invoice."""
        po = _create_fully_received_po(client)
        inv = _create_invoice(client, 1, po["id"], "INV-001", "1350.00").json()
        resp = client.post(f"/invoices/{inv['id']}/approve")
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "INVOICE_NOT_MATCHED"

    def test_approve_idempotent(self, client):
        """3c — re-approving returns 200, no duplicate GL rows."""
        po = _create_fully_received_po(client)
        inv = _create_invoice(client, 1, po["id"], "INV-001", "1350.00").json()
        client.post(f"/invoices/{inv['id']}/match")
        client.post(f"/invoices/{inv['id']}/approve")
        resp = client.post(f"/invoices/{inv['id']}/approve")
        assert resp.status_code == 200
        assert len(resp.json()["gl_entries"]) == 2  # not 4


# --------------- 4a-b: Vendor exposure ---------------

class TestVendorExposure:
    def test_exposure_with_approved_invoices(self, client):
        """4a — vendor 1 exposure after an approved invoice."""
        po = _create_fully_received_po(client)
        inv = _create_invoice(client, 1, po["id"], "INV-001", "1350.00").json()
        client.post(f"/invoices/{inv['id']}/match")
        client.post(f"/invoices/{inv['id']}/approve")
        resp = client.get("/vendors/1/exposure")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vendor_id"] == 1
        assert float(data["total_outstanding_ap"]) == 1350.00
        assert float(data["credit_limit"]) == 50000.00

    def test_exposure_no_invoices(self, client):
        """4b — vendor with no invoices."""
        resp = client.get("/vendors/2/exposure")
        assert resp.status_code == 200
        assert float(resp.json()["total_outstanding_ap"]) == 0
