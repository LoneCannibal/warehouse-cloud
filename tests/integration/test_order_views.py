"""The Job Scheduler's read-only views for the Warehouse Console, on a real PostgreSQL."""
from __future__ import annotations


def order(order_id, zones=("A",), priority=0):
    return {
        "order_id": order_id,
        "priority": priority,
        "lines": [{"sku": f"SKU-{z}001", "quantity": 1, "zone": z, "bin": f"{z}-03-05"} for z in zones],
    }


def task(store, task_id):
    return next(t for t in store.pending(100) if t["task_id"] == task_id)


def test_statuses_follow_an_order_through_picking_and_packing(store):
    store.set_epoch(1)
    store.create_order(order("o1", zones=("A", "B")))
    store.create_order(order("o2"))

    first = {s["order_id"]: s for s in store.order_statuses(["o1", "o2", "missing"])}
    assert set(first) == {"o1", "o2"}
    assert first["o1"]["status"] == "ACCEPTED"
    assert first["o1"]["tasks_total"] == 2 and first["o1"]["tasks_done"] == 0

    pick = task(store, "o1.PICK.A")
    store.assign(pick, "picker-1", 1)
    assert store.order_statuses(["o1"])[0]["status"] == "PICKING"

    store.complete("o1.PICK.A", "picker-1", 1)
    picked = store.order_statuses(["o1"])[0]
    assert picked["tasks_done"] == 1 and picked["max_attempt"] == 1


def test_a_reclaimed_task_raises_the_orders_attempt(store):
    store.set_epoch(1)
    store.create_order(order("o1"))
    pick = task(store, "o1.PICK.A")
    store.assign(pick, "picker-gone", 1)
    store.abandon({**pick, "worker_id": "picker-gone"})

    assert store.order_statuses(["o1"])[0]["max_attempt"] == 2


def test_no_ids_means_no_query(store):
    assert store.order_statuses([]) == []


def test_the_floor_shows_assigned_tasks_with_their_bins(store):
    store.set_epoch(1)
    store.create_order(order("o1", zones=("A", "C")))
    store.create_order(order("o2"))
    store.assign(task(store, "o1.PICK.C"), "picker-1", 1)

    floor = store.floor()

    assert [(t["task_id"], t["worker_id"], t["bins"]) for t in floor["tasks"]] == [("o1.PICK.C", "picker-1", ["C-03-05"])]
    assert floor["waiting"] == {"PICK": 2}
    assert floor["orders"] == {"open": 2, "dispatched": 0}


def test_an_idle_floor_is_empty(store):
    assert store.floor() == {"tasks": [], "waiting": {}, "orders": {"open": 0, "dispatched": 0}}
