"""Kafka topic names and event constructors. Schemas live in contracts/."""
from __future__ import annotations

import time
import uuid

TOPIC_ORDERS = "warehouse.orders"
TOPIC_TASK_EVENTS = "warehouse.task-events"
TOPIC_RESULTS = "warehouse.analytics.results"

ORDER_ACCEPTED = "ORDER_ACCEPTED"
TASK_CREATED = "TASK_CREATED"
TASK_ASSIGNED = "TASK_ASSIGNED"
TASK_STARTED = "TASK_STARTED"
TASK_COMPLETED = "TASK_COMPLETED"
TASK_ABANDONED = "TASK_ABANDONED"
TASK_RECLAIMED = "TASK_RECLAIMED"
SHIPMENT_DISPATCHED = "SHIPMENT_DISPATCHED"


def now_ms() -> int:
    return int(time.time() * 1000)


def task_event(
    event_type: str,
    task_id: str,
    order_id: str,
    task_type: str,
    zone: str,
    worker_id: str | None = None,
    attempt: int = 0,
    recovery_ms: int | None = None,
) -> dict:
    return {
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "task_id": task_id,
        "order_id": order_id,
        "task_type": task_type,
        "zone": zone,
        "worker_id": worker_id,
        "attempt": attempt,
        "recovery_ms": recovery_ms,
        "ts": now_ms(),
    }


def order_event(order_id: str, customer: str, priority: int, lines: list[dict]) -> dict:
    return {
        "event_id": uuid.uuid4().hex,
        "event_type": ORDER_ACCEPTED,
        "order_id": order_id,
        "customer": customer,
        "priority": priority,
        "lines": lines,
        "ts": now_ms(),
    }
