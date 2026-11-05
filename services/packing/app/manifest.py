"""Builds the package manifest that hands an order over to Dispatch."""
from __future__ import annotations

from datetime import datetime, timezone

UNIT_WEIGHT_KG = 0.4
BOX_SIZES = ((5, "SMALL"), (20, "MEDIUM"), (60, "LARGE"))


def choose_box(units: int) -> str:
    for capacity, name in BOX_SIZES:
        if units <= capacity:
            return name
    return "PALLET"


def package_id(order_id: str) -> str:
    # Derived from the order, so a reclaimed task rewrites the same object and
    # Dispatch sees one package rather than two.
    return f"pkg-{order_id}"


def manifest_key(order_id: str) -> str:
    return f"manifests/{order_id}/{package_id(order_id)}.json"


def build_manifest(order_id: str, lines: list[dict], packed_by: str, attempt: int) -> dict:
    units = sum(line["quantity"] for line in lines)
    return {
        "order_id": order_id,
        "package_id": package_id(order_id),
        "box": choose_box(units),
        "units": units,
        "weight_kg": round(units * UNIT_WEIGHT_KG, 2),
        "lines": lines,
        "packed_by": packed_by,
        "attempt": attempt,
        "packed_at": datetime.now(timezone.utc).isoformat(),
    }
