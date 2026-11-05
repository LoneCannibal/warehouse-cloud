"""Worker Registry: tracks worker availability and capabilities."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from app.registry import Registry
from warehouse_common import db, gcp, grpc_util, zk
from warehouse_common.rpc import workers_pb2, workers_pb2_grpc
from warehouse_common.settings import Settings
from warehouse_common.web import create_app

log = logging.getLogger(__name__)
settings = Settings.from_env("worker-registry")
state: dict = {}


class WorkerRegistryServicer(workers_pb2_grpc.WorkerRegistryServicer):
    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    def ListAvailable(self, request, context):
        return workers_pb2.ListAvailableResponse(
            workers=[
                workers_pb2.Worker(
                    worker_id=worker["worker_id"],
                    capability=worker["capability"],
                    zones=worker["zones"],
                    slots=worker["slots"],
                )
                for worker in self._registry.available(request.capability)
            ]
        )


@asynccontextmanager
async def lifespan(_app):
    pool = db.make_pool(settings)
    db.apply_schema(pool, Path(__file__).with_name("schema.sql"))
    client = zk.connect(settings)
    workers = gcp.firestore_client(settings).collection(settings.workers_collection)
    registry = Registry(client, pool, workers)
    registry.start()
    state["registry"] = registry
    server = grpc_util.serve(
        settings.grpc_port,
        [(workers_pb2_grpc.add_WorkerRegistryServicer_to_server, WorkerRegistryServicer(registry))],
    )
    log.info("worker registry ready")
    yield
    client.stop()
    client.close()
    server.stop(grace=5)
    pool.close()


app = create_app(settings.service, lifespan)


@app.get("/v1/workers")
def list_workers() -> dict:
    return {"workers": state["registry"].all_workers()}
