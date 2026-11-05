"""Fixtures for tests that need a real ZooKeeper and PostgreSQL.

Start them with `docker compose up -d zookeeper postgres`, or set ZK_HOSTS and DB_DSN.
Each test gets its own ZooKeeper chroot and a clean scheduler schema.
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from kazoo.client import KazooClient
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "job-scheduler"))

from app.store import TaskStore  # noqa: E402
from warehouse_common import db, zk  # noqa: E402
from warehouse_common.rpc import scheduler_pb2  # noqa: E402
from warehouse_common.settings import Settings  # noqa: E402


class RecordingProducer:
    def __init__(self) -> None:
        self.sent = []

    def send(self, topic, key, value, on_delivery=None) -> None:
        self.sent.append((topic, key, value))

    def flush(self, timeout=10.0) -> int:
        return 0


class InProcessScheduler:
    """Stands in for the gRPC stub, calling the same store the real servicer calls."""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def GetTask(self, request, timeout=None):
        task = self._store.get(request.task_id)
        return scheduler_pb2.GetTaskResponse(
            found=True,
            task_id=task["task_id"],
            order_id=task["order_id"],
            task_type=task["task_type"],
            zone=task["zone"],
            attempt=task["attempt"],
            lines=[scheduler_pb2.TaskLine(**line) for line in task["lines"]],
        )

    def CompleteTask(self, request, timeout=None):
        accepted, reason = self._store.complete(request.task_id, request.worker_id, request.attempt)
        return scheduler_pb2.CompleteTaskResponse(accepted=accepted, reason=reason)


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = Settings.from_env("test")
    admin = KazooClient(hosts=base.zk_hosts)
    admin.start(timeout=15)
    chroot = f"/test-{uuid.uuid4().hex}"
    admin.ensure_path(chroot)
    admin.stop()
    admin.close()
    return replace(base, zk_hosts=f"{base.zk_hosts}{chroot}", zk_session_timeout=4.0, db_pool_size=6)


@pytest.fixture
def pool(settings):
    connection_pool: ConnectionPool = db.make_pool(settings)
    with connection_pool.connection() as conn:
        conn.execute("DROP SCHEMA IF EXISTS scheduler CASCADE")
    db.apply_schema(connection_pool, ROOT / "services" / "job-scheduler" / "app" / "schema.sql")
    yield connection_pool
    connection_pool.close()


@pytest.fixture
def store(pool) -> TaskStore:
    return TaskStore(pool)


@pytest.fixture
def clients(settings):
    """Hands out ZooKeeper clients and closes whichever ones a test left open."""
    opened = []

    def connect():
        client = zk.connect(settings)
        opened.append(client)
        return client

    yield connect
    for client in opened:
        if client.connected:
            client.stop()
        client.close()
