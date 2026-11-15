"""GET /v1/stock: the stock list the Warehouse Console shows, per zone."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main, stock


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_list(pool, zone, limit):
        calls.append((zone, limit))
        return [{"sku": f"SKU-{zone}000", "name": "Item", "zone": zone, "bin": f"{zone}-00-00", "on_hand": 10, "reserved": 2}]

    monkeypatch.setitem(main.state, "pool", object())
    monkeypatch.setattr(stock, "list_stock", fake_list)
    # No `with`, so the lifespan (database and gRPC server) does not start.
    client = TestClient(main.app)
    client.calls = calls
    return client


def test_lists_one_zone(client):
    response = client.get("/v1/stock", params={"zone": "B", "limit": 5})

    assert response.status_code == 200
    assert response.json() == {
        "zone": "B",
        "items": [{"sku": "SKU-B000", "name": "Item", "zone": "B", "bin": "B-00-00", "on_hand": 10, "reserved": 2}],
    }
    assert client.calls == [("B", 5)]


def test_the_default_is_twenty_items(client):
    client.get("/v1/stock", params={"zone": "A"})

    assert client.calls == [("A", 20)]


@pytest.mark.parametrize(
    "params",
    [{}, {"zone": "Z"}, {"zone": "AB"}, {"zone": "A", "limit": 0}, {"zone": "A", "limit": 101}],
)
def test_rejects_a_bad_zone_or_limit(client, params):
    response = client.get("/v1/stock", params=params)

    assert response.status_code == 422
    assert client.calls == []


def test_one_product_is_still_readable(client, monkeypatch):
    monkeypatch.setattr(stock, "get_stock", lambda pool, sku: {"sku": sku, "on_hand": 5})

    assert client.get("/v1/stock/SKU-A001").json() == {"sku": "SKU-A001", "on_hand": 5}
