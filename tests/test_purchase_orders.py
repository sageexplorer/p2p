"""Tests for purchase order endpoints.

Tests are written ahead of the implementation — they will fail until
the corresponding router is built. That's intentional: each passing
test confirms a checklist item.
"""
import pytest


# --------------- helpers ---------------

def _create_po(client, vendor_id=1, line_items=None):
    """Shortcut to create a draft PO."""
    if line_items is None:
        line_items = [
            {"sku": "SKU-1001", "description": "2x4x8 Pine Stud",
             "qty_ordered": 100, "unit_cost": "4.25"},
            {"sku": "SKU-1003", "description": "5lb Box Drywall Screws",
             "qty_ordered": 50, "unit_cost": "18.50"},
        ]
    return client.post("/purchase-orders", json={
        "vendor_id": vendor_id,
        "line_items": line_items,
    })


def _submit_po(client, po_id):
    return client.post(f"/purchase-orders/{po_id}/submit")


def _receive_po(client, po_id, line_items, received_by="Tester"):
    return client.post(f"/purchase-orders/{po_id}/receive", json={
        "received_by": received_by,
        "line_items": line_items,
    })


# --------------- 1a: Create draft PO ---------------

class TestCreatePO:
    def test_happy_path(self, client):
        resp = _create_po(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "DRAFT"
        assert data["vendor_id"] == 1
        assert "workflow_id" in data
        assert len(data["line_items"]) == 2
        for li in data["line_items"]:
            assert li["qty_received"] == 0

    def test_inactive_vendor_rejected(self, client):
        """1b — vendor 3 is inactive."""
        resp = _create_po(client, vendor_id=3, line_items=[
            {"sku": "SKU-2001", "description": "Concrete Mix 80lb Bag",
             "qty_ordered": 20, "unit_cost": "6.50"},
        ])
        assert resp.status_code == 400
        data = resp.json()
        assert data["error_code"] == "INACTIVE_VENDOR"
        assert data["context"]["vendor_id"] == 3


# --------------- 1c-d: Submit PO ---------------

class TestSubmitPO:
    def test_submit_draft(self, client):
        _create_po(client)
        resp = _submit_po(client, 1)
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUBMITTED"

    def test_submit_idempotent(self, client):
        """1d — submitting an already-SUBMITTED PO returns 200."""
        _create_po(client)
        _submit_po(client, 1)
        resp = _submit_po(client, 1)
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUBMITTED"

    def test_submit_received_po_rejected(self, client):
        """1i — cannot go backwards from RECEIVED to SUBMITTED."""
        resp = _create_po(client)
        po_id = resp.json()["id"]
        _submit_po(client, po_id)
        # fully receive
        lines = resp.json()["line_items"]
        _receive_po(client, po_id, [
            {"po_line_item_id": li["id"], "qty_received": li["qty_ordered"]}
            for li in lines
        ])
        resp = _submit_po(client, po_id)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "INVALID_STATUS_TRANSITION"


# --------------- 1e: Get PO detail ---------------

class TestGetPO:
    def test_get_detail(self, client):
        _create_po(client)
        resp = client.get("/purchase-orders/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "line_items" in data
        assert "workflow_id" in data

    def test_not_found(self, client):
        resp = client.get("/purchase-orders/9999")
        assert resp.status_code == 404


# --------------- 1f-h: Goods receipt ---------------

class TestGoodsReceipt:
    def test_full_receipt(self, client):
        """1f — receive all ordered quantities."""
        resp = _create_po(client)
        po = resp.json()
        _submit_po(client, po["id"])
        lines = po["line_items"]
        resp = _receive_po(client, po["id"], [
            {"po_line_item_id": li["id"], "qty_received": li["qty_ordered"]}
            for li in lines
        ])
        assert resp.status_code == 201
        # PO should now be RECEIVED
        po_resp = client.get(f"/purchase-orders/{po['id']}")
        assert po_resp.json()["status"] == "RECEIVED"

    def test_partial_receipt(self, client):
        """1g — receive less than ordered; PO stays SUBMITTED."""
        resp = client.post("/purchase-orders", json={
            "vendor_id": 2,
            "line_items": [
                {"sku": "SKU-1004", "description": "Galvanized Roofing Nails 50ct",
                 "qty_ordered": 200, "unit_cost": "8.75"},
            ],
        })
        po = resp.json()
        _submit_po(client, po["id"])
        line = po["line_items"][0]
        resp = _receive_po(client, po["id"], [
            {"po_line_item_id": line["id"], "qty_received": 100},
        ])
        assert resp.status_code == 201
        po_resp = client.get(f"/purchase-orders/{po['id']}")
        assert po_resp.json()["status"] == "SUBMITTED"

    def test_over_receipt_rejected(self, client):
        """1h — qty exceeds remaining capacity."""
        resp = client.post("/purchase-orders", json={
            "vendor_id": 2,
            "line_items": [
                {"sku": "SKU-1004", "description": "Galvanized Roofing Nails 50ct",
                 "qty_ordered": 200, "unit_cost": "8.75"},
            ],
        })
        po = resp.json()
        _submit_po(client, po["id"])
        line = po["line_items"][0]
        # first receipt — 150
        _receive_po(client, po["id"], [
            {"po_line_item_id": line["id"], "qty_received": 150},
        ])
        # second receipt — 100 more would exceed 200
        resp = _receive_po(client, po["id"], [
            {"po_line_item_id": line["id"], "qty_received": 100},
        ])
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "OVER_RECEIPT"
