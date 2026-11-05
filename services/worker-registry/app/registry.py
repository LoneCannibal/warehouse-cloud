"""Tracks live workers by watching ephemeral znodes.

ZooKeeper is the source of truth for who is alive. This keeps a local copy for
fast gRPC reads, a Firestore mirror for the live view, and a durable Cloud SQL profile.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial

from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError
from prometheus_client import Gauge
from psycopg_pool import ConnectionPool

from warehouse_common.zk import CAPABILITIES, WORKERS

log = logging.getLogger(__name__)

LIVE_WORKERS = Gauge("warehouse_live_workers", "Workers currently in the ZooKeeper group.", ["capability"])


class Registry:
    def __init__(self, zk: KazooClient, pool: ConnectionPool, workers) -> None:
        self._zk = zk
        self._pool = pool
        self._workers = workers
        self._live: dict[str, dict[str, dict]] = {capability: {} for capability in CAPABILITIES}
        self._lock = threading.Lock()
        # One thread, so joins and leaves of a worker are mirrored in the order they happened.
        self._mirror = ThreadPoolExecutor(max_workers=1)

    def start(self) -> None:
        for capability in CAPABILITIES:
            self._zk.ChildrenWatch(f"{WORKERS}/{capability}", partial(self._on_change, capability))

    def available(self, capability: str) -> list[dict]:
        with self._lock:
            return list(self._live.get(capability, {}).values())

    def all_workers(self) -> list[dict]:
        with self._lock:
            return [worker for group in self._live.values() for worker in group.values()]

    def _on_change(self, capability: str, children: list[str]) -> None:
        current = set(children)
        with self._lock:
            known = set(self._live[capability])
        for worker_id in current - known:
            self._mirror.submit(self._joined, capability, worker_id)
        for worker_id in known - current:
            self._mirror.submit(self._left, capability, worker_id)

    def _joined(self, capability: str, worker_id: str) -> None:
        try:
            data, _ = self._zk.retry(self._zk.get, f"{WORKERS}/{capability}/{worker_id}")
        except NoNodeError:
            return
        worker = json.loads(data)
        with self._lock:
            self._live[capability][worker_id] = worker
            LIVE_WORKERS.labels(capability).set(len(self._live[capability]))
        log.info("worker joined", extra={"worker_id": worker_id})
        self._guarded(self._store_joined, worker)

    def _left(self, capability: str, worker_id: str) -> None:
        with self._lock:
            self._live[capability].pop(worker_id, None)
            LIVE_WORKERS.labels(capability).set(len(self._live[capability]))
        log.warning("worker left", extra={"worker_id": worker_id})
        self._guarded(self._store_left, worker_id)

    def _store_joined(self, worker: dict) -> None:
        self._workers.document(worker["worker_id"]).set(
            {
                "worker_id": worker["worker_id"],
                "capability": worker["capability"],
                "zones": worker["zones"],
                "slots": worker["slots"],
                "status": "AVAILABLE",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO workers.profiles (worker_id, capability, zones, slots)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (worker_id) DO UPDATE SET last_seen = now()
                """,
                (worker["worker_id"], worker["capability"], worker["zones"], worker["slots"]),
            )

    def _store_left(self, worker_id: str) -> None:
        # merge keeps the profile fields written when the worker joined.
        self._workers.document(worker_id).set(
            {"status": "OFFLINE", "updated_at": datetime.now(timezone.utc).isoformat()},
            merge=True,
        )

    @staticmethod
    def _guarded(action, argument) -> None:
        # The in-memory view is what scheduling needs, so a storage outage must not stop it.
        try:
            action(argument)
        except Exception:
            log.exception("could not mirror worker state")
