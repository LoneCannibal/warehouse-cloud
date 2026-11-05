"""Keeps the Python event constructors in step with contracts/, which Java also relies on."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from warehouse_common import events

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


def schema(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text())


def test_every_task_event_type_matches_the_contract():
    contract = schema("task-event.schema.json")
    for event_type in contract["properties"]["event_type"]["enum"]:
        event = events.task_event(event_type, "t1", "o1", "PICK", "A", "w1", 2, 1500)
        jsonschema.validate(event, contract)


def test_null_worker_and_recovery_are_allowed():
    jsonschema.validate(
        events.task_event(events.TASK_CREATED, "t1", "o1", "PICK", "A"),
        schema("task-event.schema.json"),
    )


def test_order_event_matches_the_contract():
    line = {"sku": "SKU-A001", "quantity": 1, "zone": "A", "bin": "A-01-01"}
    jsonschema.validate(events.order_event("o1", "customer", 0, [line]), schema("order-event.schema.json"))
