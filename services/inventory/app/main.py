"""Inventory Service: tracks warehouse stock.

gRPC serves the synchronous calls from Order Intake and Picking.
REST serves stock lookups, health and metrics.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, Query

from app import stock
from warehouse_common import db, grpc_util
from warehouse_common.rpc import inventory_pb2, inventory_pb2_grpc
from warehouse_common.settings import Settings
from warehouse_common.web import create_app

log = logging.getLogger(__name__)
settings = Settings.from_env("inventory")
state: dict = {}


class InventoryServicer(inventory_pb2_grpc.InventoryServicer):
    def __init__(self, pool) -> None:
        self._pool = pool

    def ReserveStock(self, request, context):
        lines = [(line.sku, line.quantity) for line in request.lines]
        reserved, reason, allocations = stock.reserve(self._pool, request.order_id, lines)
        if not reserved:
            log.info("reservation refused: %s", reason, extra={"order_id": request.order_id})
        return inventory_pb2.ReserveStockResponse(
            reserved=reserved,
            reason=reason,
            allocations=[inventory_pb2.Allocation(**allocation) for allocation in allocations],
        )

    def ReleaseStock(self, request, context):
        return inventory_pb2.ReleaseStockResponse(released=stock.release(self._pool, request.order_id))

    def ConfirmPick(self, request, context):
        lines = [(line.sku, line.quantity) for line in request.lines]
        stock.confirm_pick(self._pool, request.task_id, lines)
        return inventory_pb2.ConfirmPickResponse(confirmed=True)


@asynccontextmanager
async def lifespan(_app):
    pool = db.make_pool(settings)
    db.apply_schema(pool, Path(__file__).with_name("schema.sql"))
    stock.seed(pool)
    state["pool"] = pool
    server = grpc_util.serve(
        settings.grpc_port,
        [(inventory_pb2_grpc.add_InventoryServicer_to_server, InventoryServicer(pool))],
    )
    log.info("inventory ready")
    yield
    server.stop(grace=5)
    pool.close()


app = create_app(settings.service, lifespan)


@app.get("/v1/stock")
def list_stock(zone: str = Query(min_length=1, max_length=1), limit: int = Query(default=20, ge=1, le=100)) -> dict:
    if zone not in stock.ZONES:
        raise HTTPException(status_code=422, detail=f"zone must be one of {', '.join(stock.ZONES)}")
    return {"zone": zone, "items": stock.list_stock(state["pool"], zone, limit)}


@app.get("/v1/stock/{sku}")
def read_stock(sku: str) -> dict:
    row = stock.get_stock(state["pool"], sku)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown sku")
    return row
