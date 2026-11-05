"""Job Scheduler: creates tasks, assigns work and manages leases.

Runs with three replicas, of which one leads. It has no HPA, because assignment is
single-writer by design: extra replicas are standbys, not extra throughput.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException
from prometheus_client import Counter

from app.leader import Leader
from app.store import TaskStore
from warehouse_common import db, grpc_util, zk
from warehouse_common.kafka import EventProducer
from warehouse_common.rpc import scheduler_pb2, scheduler_pb2_grpc, workers_pb2_grpc
from warehouse_common.settings import Settings
from warehouse_common.web import create_app

log = logging.getLogger(__name__)
settings = Settings.from_env("job-scheduler")
state: dict = {}

FENCED = Counter("warehouse_fenced_completions_total", "Completions rejected from stale attempts.")


class SchedulerServicer(scheduler_pb2_grpc.JobSchedulerServicer):
    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def GetTask(self, request, context):
        task = self._store.get(request.task_id)
        if task is None:
            return scheduler_pb2.GetTaskResponse(found=False)
        return scheduler_pb2.GetTaskResponse(
            found=True,
            task_id=task["task_id"],
            order_id=task["order_id"],
            task_type=task["task_type"],
            zone=task["zone"],
            attempt=task["attempt"],
            lines=[scheduler_pb2.TaskLine(**line) for line in task["lines"]],
        )

    def CompleteTask(self, request, context):
        accepted, reason = self._store.complete(request.task_id, request.worker_id, request.attempt)
        if not accepted:
            FENCED.inc()
            log.warning(
                "completion fenced: %s",
                reason,
                extra={"task_id": request.task_id, "worker_id": request.worker_id, "attempt": request.attempt},
            )
        return scheduler_pb2.CompleteTaskResponse(accepted=accepted, reason=reason)


@asynccontextmanager
async def lifespan(_app):
    pool = db.make_pool(settings)
    db.apply_schema(pool, Path(__file__).with_name("schema.sql"))
    store = TaskStore(pool)
    client = zk.connect(settings)
    producer = EventProducer(settings)
    workers_channel = grpc_util.channel(settings.workers_target)
    workers = workers_pb2_grpc.WorkerRegistryStub(workers_channel)
    server = grpc_util.serve(
        settings.grpc_port,
        [(scheduler_pb2_grpc.add_JobSchedulerServicer_to_server, SchedulerServicer(store))],
    )
    leader = Leader(settings, client, store, producer, workers)
    leader.start()
    state.update(store=store, leader=leader)
    log.info("job scheduler ready")
    yield
    client.stop()
    client.close()
    server.stop(grace=5)
    producer.flush()
    workers_channel.close()
    pool.close()


app = create_app(settings.service, lifespan)


@app.get("/v1/orders/{order_id}/tasks")
def order_tasks(order_id: str) -> dict:
    view = state["store"].order_view(order_id)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown order")
    return view


@app.get("/v1/queue")
def queue() -> dict:
    return state["store"].queue_depth()


@app.get("/v1/leader")
def leader_status() -> dict:
    leader = state["leader"]
    return {"replica": settings.worker_id, "leading": leader.leading, "epoch": leader.epoch}
