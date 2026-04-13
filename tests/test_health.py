"""Baseline test — health check and seed data."""


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_products_seeded(client):
    resp = client.get("/products")
    assert resp.status_code == 200
    products = resp.json()
    assert len(products) == 8
