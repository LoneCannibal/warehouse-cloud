"""Worker side of the ZooKeeper protocol, shared by Picking and Packing.

Each pod is one simulated worker station. It joins the group with an ephemeral
znode, watches its own assignment path, and holds an ephemeral lease while it
works a task. If the pod dies, its session ends and ZooKeeper removes both nodes,
which is how the Job Scheduler finds out the task was abandoned.

Session liveness does not prove progress, because a stuck process keeps its
session open. So a lease also carries expires_at, which the worker keeps renewing.
A worker that stops renewing loses the task when the leader's reaper removes it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from kazoo.client import KazooClient, KazooState
from kazoo.exceptions import BadVersionError, KazooException, NodeExistsError, NoNodeError
from prometheus_client import Counter, Gauge

from warehouse_common.events import TASK_STARTED, TOPIC_TASK_EVENTS, task_event
from warehouse_common.kafka import EventProducer
from warehouse_common.rpc import scheduler_pb2
from warehouse_common.settings import Settings
from warehouse_common.zk import (
    ASSIGNMENTS,
    DEFAULT_LEASE_TTL_SECONDS,
    LEASE_TTL,
    LEASES,
    WORKERS,
)

log = logging.getLogger(__name__)

LEASE_CLAIM_ATTEMPTS = 20
LEASE_CLAIM_DELAY_SECONDS = 0.25

TASKS_FINISHED = Counter("warehouse_worker_tasks_total", "Tasks this worker finished.", ["task_type", "outcome"])
LEASES_HELD = Gauge("warehouse_worker_leases_held", "Leases this worker currently holds.")


class TaskWorker:
    def __init__(
        self,
        settings: Settings,
        zk: KazooClient,
        capability: str,
        zones: list[str],
        process: Callable[[scheduler_pb2.GetTaskResponse], None],
        scheduler,
        producer: EventProducer,
    ) -> None:
        self.worker_id = settings.worker_id
        self._zk = zk
        self._capability = capability
        self._zones = zones
        self._slots = settings.worker_slots
        self._process = process
        self._scheduler = scheduler
        self._producer = producer
        self._executor = ThreadPoolExecutor(max_workers=self._slots)
        self._assignments_path = f"{ASSIGNMENTS}/{self.worker_id}"
        self._seen: set[str] = set()
        self._held: dict[str, int] = {}
        self._held_lock = threading.Lock()
        self._lease_ttl = float(DEFAULT_LEASE_TTL_SECONDS)
        self._running = threading.Event()
        self._running.set()
        self._stop = threading.Event()

    def start(self) -> None:
        self._zk.ensure_path(self._assignments_path)
        self._zk.add_listener(self._on_state)
        self._register()
        self._zk.DataWatch(LEASE_TTL, self._on_ttl)
        self._zk.ChildrenWatch(self._assignments_path, self._on_assignments)
        threading.Thread(target=self._renew_loop, name="lease-renewer", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._running.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # Failure injection for the demo: the pod stays alive and keeps its session
    # but stops renewing leases and reporting, like a stuck process.
    def hang(self) -> None:
        log.warning("worker hanging", extra={"worker_id": self.worker_id})
        self._running.clear()

    def resume(self) -> None:
        log.warning("worker resuming", extra={"worker_id": self.worker_id})
        self._running.set()

    @property
    def hung(self) -> bool:
        return not self._running.is_set()

    def _register(self) -> None:
        data = json.dumps(
            {
                "worker_id": self.worker_id,
                "capability": self._capability,
                "zones": self._zones,
                "slots": self._slots,
            }
        ).encode()
        try:
            self._zk.retry(self._zk.create, f"{WORKERS}/{self._capability}/{self.worker_id}", data, ephemeral=True)
        except NodeExistsError:
            pass
        log.info("registered in ZooKeeper", extra={"worker_id": self.worker_id})

    def _on_state(self, state: str) -> None:
        if state == KazooState.LOST:
            # The session is gone, so ZooKeeper removed every lease it held.
            with self._held_lock:
                self._held.clear()
            LEASES_HELD.set(0)
        elif state == KazooState.CONNECTED:
            self._zk.handler.spawn(self._register)

    def _on_ttl(self, data, _stat) -> None:
        if data:
            self._lease_ttl = float(data.decode())
            log.info("lease ttl is now %s seconds", self._lease_ttl)

    def _on_assignments(self, children: list[str]) -> None:
        current = set(children)
        for name in current - self._seen:
            self._executor.submit(self._run, name)
        self._seen = current

    def _run(self, name: str) -> None:
        task_id, attempt = name.rsplit("@", 1)
        attempt = int(attempt)
        assignment = f"{self._assignments_path}/{name}"
        context = {"task_id": task_id, "worker_id": self.worker_id, "attempt": attempt}

        # The leader deletes an offer when it reclaims the task. A worker that was
        # hung can reach this point long after that, and must not claim the lease.
        if self._zk.retry(self._zk.exists, assignment) is None or not self._claim(task_id, attempt):
            self._delete(assignment)
            return

        outcome = "failed"
        try:
            task = self._scheduler.GetTask(scheduler_pb2.GetTaskRequest(task_id=task_id), timeout=5)
            if task.attempt != attempt:
                log.info("skipping a superseded attempt", extra=context)
                return
            self._producer.send(
                TOPIC_TASK_EVENTS,
                task.order_id,
                task_event(
                    TASK_STARTED, task_id, task.order_id, task.task_type, task.zone, self.worker_id, attempt
                ),
            )
            self._process(task)
            # A hung worker stops here, after the work and before reporting. If it
            # resumes after losing the lease, the scheduler must reject its result.
            self._running.wait()
            response = self._scheduler.CompleteTask(
                scheduler_pb2.CompleteTaskRequest(task_id=task_id, worker_id=self.worker_id, attempt=attempt),
                timeout=5,
            )
            if response.accepted:
                outcome = "completed"
                log.info("task completed", extra=context)
            else:
                outcome = "fenced"
                log.warning("completion rejected: %s", response.reason, extra=context)
            TASKS_FINISHED.labels(task.task_type, outcome).inc()
        except Exception:
            log.exception("task failed, releasing the lease so it can be reclaimed", extra=context)
        finally:
            self._release(task_id, attempt)
            self._delete(assignment)

    def _claim(self, task_id: str, attempt: int) -> bool:
        path = f"{LEASES}/{task_id}"
        lease = self._lease_value(attempt)
        # The lease from a dead attempt can outlive it briefly, so retry instead of failing.
        for _ in range(LEASE_CLAIM_ATTEMPTS):
            try:
                self._zk.retry(self._zk.create, path, lease, ephemeral=True)
            except NodeExistsError:
                # A retried create can find the node its own lost reply created.
                if self._owned_lease(task_id, attempt) is None:
                    time.sleep(LEASE_CLAIM_DELAY_SECONDS)
                    continue
            except NoNodeError:
                return False
            with self._held_lock:
                self._held[task_id] = attempt
                LEASES_HELD.set(len(self._held))
            return True
        log.warning("could not claim lease", extra={"task_id": task_id, "attempt": attempt})
        return False

    def _lease_value(self, attempt: int) -> bytes:
        return json.dumps(
            {"worker": self.worker_id, "attempt": attempt, "expires_at": time.time() + self._lease_ttl}
        ).encode()

    def _owned_lease(self, task_id: str, attempt: int):
        """Returns the lease znode stat if this worker still holds this attempt.

        Znode versions restart on a new node, so a version check alone cannot tell
        our lease from the one the next worker created. The contents can.
        """
        try:
            data, stat = self._zk.retry(self._zk.get, f"{LEASES}/{task_id}")
        except NoNodeError:
            return None
        lease = json.loads(data)
        if lease["worker"] == self.worker_id and lease["attempt"] == attempt:
            return stat
        return None

    def _release(self, task_id: str, attempt: int) -> None:
        with self._held_lock:
            self._held.pop(task_id, None)
            LEASES_HELD.set(len(self._held))
        try:
            stat = self._owned_lease(task_id, attempt)
            if stat is not None:
                self._zk.retry(self._zk.delete, f"{LEASES}/{task_id}", version=stat.version)
        except (NoNodeError, BadVersionError):
            pass
        except KazooException:
            # The lease is ephemeral or will expire, so the leader recovers either way.
            log.warning("could not release lease", extra={"task_id": task_id, "attempt": attempt})

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._lease_ttl / 3):
            if not self._running.is_set():
                continue
            with self._held_lock:
                held = dict(self._held)
            for task_id, attempt in held.items():
                self._renew(task_id, attempt)

    def _renew(self, task_id: str, attempt: int) -> None:
        try:
            stat = self._owned_lease(task_id, attempt)
            if stat is None:
                log.warning("lease lost", extra={"task_id": task_id, "attempt": attempt})
            else:
                self._zk.retry(
                    self._zk.set, f"{LEASES}/{task_id}", self._lease_value(attempt), version=stat.version
                )
        except KazooException:
            # This must not end the renewal thread, or every later lease would expire.
            log.warning("could not renew lease", extra={"task_id": task_id, "attempt": attempt})

    def _delete(self, path: str) -> None:
        try:
            self._zk.retry(self._zk.delete, path)
        except NoNodeError:
            pass
        except KazooException:
            log.warning("could not delete %s", path)
