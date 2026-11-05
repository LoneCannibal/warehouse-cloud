"""Order Intake Service: the public REST entry point for fulfilment orders.

An order is accepted once its stock is reserved over gRPC. It is then stored and
published to Kafka, where the Job Scheduler picks it up asynchronously.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import grpc
import httpx
from fastapi import Header, HTTPException, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from warehouse_common import db, grpc_util
from warehouse_common.events import TOPIC_ORDERS, order_event
from warehouse_common.kafka import EventProducer
from warehouse_common.rpc import inventory_pb2, inventory_pb2_grpc
from warehouse_common.settings import Settings
from warehouse_common.web import create_app

log = logging.getLogger(__name__)
settings = Settings.from_env("order-intake")
state: dict = {}


class OrderLine(BaseModel):
    sku: str = Field(min_length=1, max_length=32)
    quantity: int = Field(gt=0, le=1000)


class OrderRequest(BaseModel):
    customer: str = Field(min_length=1, max_length=100)
    lines: list[OrderLine] = Field(min_length=1, max_length=50)
    priority: int = Field(default=0, ge=0, le=9)


@asynccontextmanager
async def lifespan(_app):
    pool = db.make_pool(settings)
    db.apply_schema(pool, Path(__file__).with_name("schema.sql"))
    channel = grpc_util.channel(settings.inventory_target)
    state.update(
        pool=pool,
        inventory=inventory_pb2_grpc.InventoryStub(channel),
        producer=EventProducer(settings),
        scheduler=httpx.Client(base_url=settings.scheduler_url, timeout=2.0),
    )
    log.info("order intake ready")
    yield
    state["producer"].flush()
    state["scheduler"].close()
    channel.close()
    pool.close()


app = create_app(settings.service, lifespan)


def _find_by_key(key: str) -> dict | None:
    with state["pool"].connection() as conn:
        return conn.execute(
            "SELECT order_id, customer, priority, lines FROM orders.orders WHERE idempotency_key = %s",
            (key,),
        ).fetchone()


def _store(order_id: str, key: str | None, order: OrderRequest, lines: list[dict]) -> None:
    with state["pool"].connection() as conn:
        conn.execute(
            """
            INSERT INTO orders.orders (order_id, idempotency_key, customer, priority, lines)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (order_id, key, order.customer, order.priority, Jsonb(lines)),
        )


@app.post("/v1/orders", status_code=202)
def create_order(
    order: OrderRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None),
) -> dict:
    if idempotency_key:
        existing = _find_by_key(idempotency_key)
        if existing:
            response.status_code = 200
            return {"order_id": existing["order_id"], "status": "ACCEPTED", "duplicate": True}

    order_id = f"ord-{uuid.uuid4().hex[:16]}"
    request = inventory_pb2.ReserveStockRequest(
        order_id=order_id,
        lines=[inventory_pb2.OrderLine(sku=line.sku, quantity=line.quantity) for line in order.lines],
    )
    try:
        reservation = state["inventory"].ReserveStock(request, timeout=3)
    except grpc.RpcError as error:
        log.error("inventory unavailable: %s", error.code(), extra={"order_id": order_id})
        raise HTTPException(status_code=503, detail="inventory unavailable") from error

    if not reservation.reserved:
        raise HTTPException(status_code=409, detail=reservation.reason)

    lines = [
        {"sku": a.sku, "quantity": a.quantity, "zone": a.zone, "bin": a.bin}
        for a in reservation.allocations
    ]
    try:
        _store(order_id, idempotency_key, order, lines)
    except UniqueViolation:
        # A concurrent retry with the same key won the insert, so give the stock back.
        state["inventory"].ReleaseStock(inventory_pb2.ReleaseStockRequest(order_id=order_id), timeout=3)
        existing = _find_by_key(idempotency_key)
        response.status_code = 200
        return {"order_id": existing["order_id"], "status": "ACCEPTED", "duplicate": True}

    state["producer"].send(
        TOPIC_ORDERS, order_id, order_event(order_id, order.customer, order.priority, lines)
    )
    log.info("order accepted", extra={"order_id": order_id})
    return {"order_id": order_id, "status": "ACCEPTED"}


@app.get("/v1/orders/{order_id}")
def read_order(order_id: str) -> dict:
    with state["pool"].connection() as conn:
        order = conn.execute(
            "SELECT order_id, customer, priority, lines, created_at FROM orders.orders WHERE order_id = %s",
            (order_id,),
        ).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="unknown order")

    # Fulfilment state lives in the Job Scheduler. If it is unreachable the order
    # itself is still readable, so this degrades instead of failing.
    try:
        reply = state["scheduler"].get(f"/v1/orders/{order_id}/tasks")
        if reply.status_code == 404:
            # The event is still in Kafka, so the scheduler has not seen this order yet.
            order["fulfilment"] = {"order_id": order_id, "status": "ACCEPTED", "tasks": []}
            return order
        reply.raise_for_status()
        order["fulfilment"] = reply.json()
    except httpx.HTTPError:
        log.warning("scheduler unavailable", extra={"order_id": order_id})
        order["fulfilment"] = None
    return order
