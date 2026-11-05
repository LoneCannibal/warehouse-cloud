"""Packing Service: processes packing tasks and records package status.

Finishing a task writes a manifest to Cloud Storage. That upload is what triggers
Dispatch: Cloud Storage notifies Pub/Sub, and Pub/Sub pushes it to the function.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

from app.manifest import build_manifest, manifest_key
from warehouse_common import gcp, grpc_util, zk
from warehouse_common.kafka import EventProducer
from warehouse_common.rpc import scheduler_pb2_grpc
from warehouse_common.settings import Settings
from warehouse_common.web import create_app
from warehouse_common.worker import TaskWorker

log = logging.getLogger(__name__)
settings = Settings.from_env("packing")
state: dict = {}

ZONES = os.environ.get("WORKER_ZONES", "P").split(",")


@asynccontextmanager
async def lifespan(_app):
    client = zk.connect(settings)
    producer = EventProducer(settings)
    scheduler_channel = grpc_util.channel(settings.scheduler_target)
    bucket = gcp.data_bucket(settings)

    def process(task) -> None:
        lines = [
            {"sku": line.sku, "quantity": line.quantity, "zone": line.zone, "bin": line.bin}
            for line in task.lines
        ]
        manifest = build_manifest(task.order_id, lines, settings.worker_id, task.attempt)
        bucket.blob(manifest_key(task.order_id)).upload_from_string(
            json.dumps(manifest), content_type="application/json"
        )

    worker = TaskWorker(
        settings,
        client,
        capability="packing",
        zones=ZONES,
        process=process,
        scheduler=scheduler_pb2_grpc.JobSchedulerStub(scheduler_channel),
        producer=producer,
    )
    worker.start()
    state["worker"] = worker
    log.info("packing ready", extra={"worker_id": worker.worker_id})
    yield
    worker.stop()
    client.stop()
    client.close()
    producer.flush()
    scheduler_channel.close()


app = create_app(settings.service, lifespan)
