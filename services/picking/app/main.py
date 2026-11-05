"""Picking Service: processes picking tasks and records completion.

Each pod is one worker station. Scaling this Deployment adds warehouse capacity:
a new pod joins ZooKeeper and the leader starts assigning to it, with no config
change. The chaos routes are reachable only inside the cluster.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from app.route import plan_route
from warehouse_common import grpc_util, zk
from warehouse_common.kafka import EventProducer
from warehouse_common.rpc import inventory_pb2, inventory_pb2_grpc, scheduler_pb2_grpc
from warehouse_common.settings import Settings
from warehouse_common.web import create_app
from warehouse_common.worker import TaskWorker

log = logging.getLogger(__name__)
settings = Settings.from_env("picking")
state: dict = {}

ROUTE_STOPS = int(os.environ.get("ROUTE_STOPS", "90"))
ZONES = os.environ.get("WORKER_ZONES", "A,B,C").split(",")


@asynccontextmanager
async def lifespan(_app):
    client = zk.connect(settings)
    producer = EventProducer(settings)
    inventory_channel = grpc_util.channel(settings.inventory_target)
    scheduler_channel = grpc_util.channel(settings.scheduler_target)
    inventory = inventory_pb2_grpc.InventoryStub(inventory_channel)

    def process(task) -> None:
        plan_route([line.bin for line in task.lines], ROUTE_STOPS, task.task_id)
        inventory.ConfirmPick(
            inventory_pb2.ConfirmPickRequest(
                task_id=task.task_id,
                order_id=task.order_id,
                lines=[inventory_pb2.OrderLine(sku=line.sku, quantity=line.quantity) for line in task.lines],
            ),
            timeout=5,
        )

    worker = TaskWorker(
        settings,
        client,
        capability="picking",
        zones=ZONES,
        process=process,
        scheduler=scheduler_pb2_grpc.JobSchedulerStub(scheduler_channel),
        producer=producer,
    )
    worker.start()
    state["worker"] = worker
    log.info("picking ready", extra={"worker_id": worker.worker_id})
    yield
    worker.stop()
    client.stop()
    client.close()
    producer.flush()
    inventory_channel.close()
    scheduler_channel.close()


app = create_app(settings.service, lifespan)


@app.post("/chaos/hang")
def hang() -> dict:
    state["worker"].hang()
    return {"worker_id": state["worker"].worker_id, "hung": True}


@app.post("/chaos/resume")
def resume() -> dict:
    state["worker"].resume()
    return {"worker_id": state["worker"].worker_id, "hung": False}
