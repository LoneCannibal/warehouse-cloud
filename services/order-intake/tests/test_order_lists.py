"""GET /v1/orders and GET /v1/floor: the lists the Warehouse Console reads."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

ORDERS = [
    {"order_id": "ord-2", "customer": "ana", "priority": 0, "lines": [], "created_at": "2026-11-15T17:00:02Z"},
    {"order_id": "ord-1", "customer": "raj", "priority": 9, "lines": [], "created_at": "2026-11-15T17:00:01Z"},
]


class FakePool:
    """Records each query and returns the orders the test set."""

    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def connection(self):
        pool = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                pool.queries.append((sql, params))
                return self

            def fetchall(self):
                return [dict(row) for row in pool.rows]

        return Conn()


class FakeScheduler:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return self.handler(path, params)


def reply(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("GET", "http://scheduler"))


@pytest.fixture
def setup(monkeypatch):
    def install(rows, handler):
        pool, scheduler = FakePool(rows), FakeScheduler(handler)
        monkeypatch.setitem(main.state, "pool", pool)
        monkeypatch.setitem(main.state, "scheduler", scheduler)
        # No `with`, so the lifespan (database, Kafka, gRPC) does not start.
        return TestClient(main.app), pool, scheduler

    return install


def test_recent_orders_carry_their_fulfilment_status(setup):
    statuses = {"orders": [{"order_id": "ord-1", "status": "DISPATCHED", "tasks_total": 2, "tasks_done": 2,
                            "max_attempt": 2, "dispatched_at": "2026-11-15T17:00:09Z"}]}
    client, pool, scheduler = setup(ORDERS, lambda path, params: reply(200, statuses))

    body = client.get("/v1/orders", params={"limit": 2}).json()

    assert [o["order_id"] for o in body["orders"]] == ["ord-2", "ord-1"]
    assert body["orders"][1]["fulfilment"]["status"] == "DISPATCHED"
    assert body["orders"][1]["fulfilment"]["max_attempt"] == 2
    # Not known to the scheduler yet: the order event is still in Kafka.
    assert body["orders"][0]["fulfilment"]["status"] == "ACCEPTED"
    assert scheduler.calls == [("/v1/orders/status", {"ids": "ord-2,ord-1"})]
    assert pool.queries[0][1] == (2,)


def test_specific_orders_are_looked_up_by_id(setup):
    client, pool, _ = setup(ORDERS, lambda path, params: reply(200, {"orders": []}))

    client.get("/v1/orders", params={"ids": "ord-1,ord-2,"})

    sql, params = pool.queries[0]
    assert "ANY(%s)" in sql
    assert params == (["ord-1", "ord-2"],)


def test_orders_stay_readable_when_the_scheduler_is_down(setup):
    def down(path, params):
        raise httpx.ConnectError("refused")

    client, _, _ = setup(ORDERS, down)

    body = client.get("/v1/orders").json()

    assert len(body["orders"]) == 2
    assert all(order["fulfilment"] is None for order in body["orders"])


def test_no_orders_means_no_scheduler_call(setup):
    client, _, scheduler = setup([], lambda path, params: reply(200, {"orders": []}))

    assert client.get("/v1/orders").json() == {"orders": []}
    assert scheduler.calls == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"ids": "x" * 4001}])
def test_rejects_an_unreasonable_list_request(setup, params):
    client, _, _ = setup(ORDERS, lambda path, params: reply(200, {"orders": []}))

    assert client.get("/v1/orders", params=params).status_code == 422


def test_the_floor_comes_from_the_scheduler(setup):
    floor = {"tasks": [], "waiting": {"PICK": 3}, "orders": {"open": 3, "dispatched": 10}}
    client, _, scheduler = setup([], lambda path, params: reply(200, floor))

    assert client.get("/v1/floor").json() == floor
    assert scheduler.calls == [("/v1/floor", None)]


def test_the_floor_reports_a_missing_scheduler_as_503(setup):
    client, _, _ = setup([], lambda path, params: reply(503, {"detail": "down"}))

    assert client.get("/v1/floor").status_code == 503
