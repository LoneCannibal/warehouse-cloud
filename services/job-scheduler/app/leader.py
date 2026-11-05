"""The work only the elected leader does.

Every Job Scheduler replica serves reads and CompleteTask. Assignment, lease
reaping, the Kafka consumer and the outbox relay run on the leader alone, and
they stop as soon as leadership is lost.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from kazoo.client import KazooClient
from kazoo.exceptions import NodeExistsError, NoNodeError
from prometheus_client import Counter, Gauge, Histogram

from app.store import TaskStore
from warehouse_common import events
from warehouse_common.kafka import EventProducer, consume
from warehouse_common.rpc import workers_pb2
from warehouse_common.settings import Settings
from warehouse_common.zk import ASSIGNMENTS, LEASES, LeaderElection

log = logging.getLogger(__name__)

CAPABILITY = {"PICK": "picking", "PACK": "packing"}
ASSIGN_BATCH = 100
# Assignment wakes on events. This timer only covers a PACK task created on another replica.
ASSIGN_IDLE_SECONDS = 0.5
REAP_INTERVAL_SECONDS = 2.0
REAP_MIN_GAP_SECONDS = 0.5
OUTBOX_BATCH = 500
OUTBOX_IDLE_SECONDS = 0.1
# How long a worker gets to create its lease after being offered a task.
CLAIM_GRACE = timedelta(seconds=5)

IS_LEADER = Gauge("warehouse_scheduler_is_leader", "1 on the replica that currently leads.")
EPOCH = Gauge("warehouse_scheduler_epoch", "Leader epoch held by this replica.")
QUEUE_DEPTH = Gauge("warehouse_queue_depth", "Tasks waiting for a worker.", ["task_type"])
ASSIGNED = Counter("warehouse_tasks_assigned_total", "Tasks assigned to a worker.", ["task_type"])
ABANDONED = Counter("warehouse_tasks_abandoned_total", "Tasks taken back from a lost worker.", ["reason"])
RECOVERY = Histogram(
    "warehouse_task_recovery_seconds",
    "Time from abandoning a task to reassigning it.",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)


class Leader:
    def __init__(self, settings: Settings, zk: KazooClient, store: TaskStore, producer: EventProducer, workers) -> None:
        self._settings = settings
        self._zk = zk
        self._store = store
        self._producer = producer
        self._workers = workers
        self._election = LeaderElection(zk, settings.worker_id, self._on_elected, self._on_revoked)
        self._stop = threading.Event()
        self._stop.set()
        self._epoch = -1
        self._lease_change = threading.Event()
        self._work_available = threading.Event()

    @property
    def leading(self) -> bool:
        return self._election.leading

    @property
    def epoch(self) -> int:
        return self._epoch

    def start(self) -> None:
        self._election.start()

    def _on_elected(self, epoch: int) -> None:
        self._epoch = epoch
        self._store.set_epoch(epoch)
        self._stop = threading.Event()
        IS_LEADER.set(1)
        EPOCH.set(epoch)

        stop = self._stop
        self._zk.ChildrenWatch(LEASES, lambda _children: self._on_lease_change(stop))
        for name, target in (
            ("consumer", self._consume),
            ("assigner", self._assign_loop),
            ("reaper", self._reap_loop),
            ("relay", self._relay_loop),
        ):
            threading.Thread(target=target, args=(stop,), name=f"leader-{name}", daemon=True).start()

    def _on_revoked(self) -> None:
        self._stop.set()
        IS_LEADER.set(0)

    def _on_lease_change(self, stop: threading.Event):
        if stop.is_set():
            return False  # Returning False cancels this ChildrenWatch.
        self._lease_change.set()
        # A lease removed means a worker slot just freed up. Waking on this instead of
        # polling matters: a 200 ms poll capped a 2-slot worker at 10 tasks a second.
        self._work_available.set()
        return None

    def _ready(self) -> bool:
        return self._election.connected.is_set()

    def _consume(self, stop: threading.Event) -> None:
        consume(
            self._settings,
            "job-scheduler",
            [events.TOPIC_ORDERS, events.TOPIC_TASK_EVENTS],
            self._on_event,
            stop,
        )

    def _on_event(self, topic: str, event: dict) -> None:
        if topic == events.TOPIC_ORDERS:
            self._store.create_order(event)
            self._work_available.set()
            log.info("tasks created", extra={"order_id": event["order_id"]})
        elif event["event_type"] == events.SHIPMENT_DISPATCHED:
            self._store.mark_dispatched(event["order_id"])
            log.info("order dispatched", extra={"order_id": event["order_id"]})

    def _assign_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._work_available.clear()
            try:
                assigned = self.assign_once() if self._ready() else 0
            except Exception:
                log.exception("assignment pass failed")
                assigned = 0
            if assigned == 0:
                self._work_available.wait(ASSIGN_IDLE_SECONDS)

    def assign_once(self) -> int:
        tasks = self._store.pending(ASSIGN_BATCH)
        QUEUE_DEPTH.clear()
        for task_type, depth in self._store.queue_depth().items():
            QUEUE_DEPTH.labels(task_type).set(depth)
        if not tasks:
            return 0

        load = defaultdict(int, self._store.in_flight())
        candidates: dict[str, list] = {}
        assigned = 0
        for task in tasks:
            capability = CAPABILITY[task["task_type"]]
            if capability not in candidates:
                candidates[capability] = list(self._list_workers(capability))
            worker = _least_loaded(candidates[capability], task["zone"], load)
            if worker is None:
                continue

            recovery_ms = self._store.assign(task, worker.worker_id, self._epoch)
            if recovery_ms is None:
                continue
            load[worker.worker_id] += 1
            assigned += 1
            ASSIGNED.labels(task["task_type"]).inc()
            if task["abandoned_at"] is not None:
                RECOVERY.observe(recovery_ms / 1000)
            self._offer(task, worker.worker_id)
        return assigned

    def _list_workers(self, capability: str) -> list:
        reply = self._workers.ListAvailable(workers_pb2.ListAvailableRequest(capability=capability), timeout=3)
        return reply.workers

    def _offer(self, task: dict, worker_id: str) -> None:
        name = f"{task['task_id']}@{task['attempt']}"
        data = json.dumps({"task_id": task["task_id"], "attempt": task["attempt"]}).encode()
        try:
            self._zk.retry(self._zk.create, f"{ASSIGNMENTS}/{worker_id}/{name}", data, makepath=True)
        except NodeExistsError:
            pass
        log.info(
            "task assigned", extra={"task_id": task["task_id"], "worker_id": worker_id, "attempt": task["attempt"]}
        )

    def _reap_loop(self, stop: threading.Event) -> None:
        # The watch gives prompt recovery. The timer covers what a watch cannot:
        # lease expiry, and anything that changed while there was no leader.
        while not stop.is_set():
            self._lease_change.wait(REAP_INTERVAL_SECONDS)
            self._lease_change.clear()
            if stop.is_set():
                return
            try:
                if self._ready():
                    self.reap_once()
            except Exception:
                log.exception("reap pass failed")
            stop.wait(REAP_MIN_GAP_SECONDS)

    def reap_once(self) -> int:
        leases = set(self._zk.retry(self._zk.get_children, LEASES))
        now = datetime.now(timezone.utc)
        reclaimed = 0
        for task in self._store.active():
            task_id = task["task_id"]
            if task_id in leases:
                if self._lease_expired(task):
                    self._abandon(task, "lease_expired")
                    reclaimed += 1
            elif now - task["assigned_at"] > CLAIM_GRACE:
                self._abandon(task, "lease_lost")
                reclaimed += 1
        return reclaimed

    def _lease_expired(self, task: dict) -> bool:
        path = f"{LEASES}/{task['task_id']}"
        try:
            data, stat = self._zk.retry(self._zk.get, path)
        except NoNodeError:
            return False
        lease = json.loads(data)
        if lease["attempt"] != task["attempt"] or lease["expires_at"] >= time.time():
            return False
        self._delete(path, version=stat.version)
        return True

    def _abandon(self, task: dict, reason: str) -> None:
        if not self._store.abandon(task):
            return
        ABANDONED.labels(reason).inc()
        self._delete(f"{ASSIGNMENTS}/{task['worker_id']}/{task['task_id']}@{task['attempt']}")
        log.warning(
            "task abandoned (%s), requeued",
            reason,
            extra={"task_id": task["task_id"], "worker_id": task["worker_id"], "attempt": task["attempt"]},
        )

    def _delete(self, path: str, version: int = -1) -> None:
        try:
            self._zk.retry(self._zk.delete, path, version=version)
        except NoNodeError:
            pass

    def _relay_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                relayed = self.relay_once()
            except Exception:
                log.exception("outbox relay failed")
                relayed = 0
            if relayed == 0:
                stop.wait(OUTBOX_IDLE_SECONDS)

    def relay_once(self) -> int:
        rows = self._store.unpublished(OUTBOX_BATCH)
        if not rows:
            return 0
        for row in rows:
            self._producer.send(row["topic"], row["key"], row["payload"])
        if self._producer.flush() > 0:
            # Rows stay in the outbox and are sent again, so consumers must be idempotent.
            log.error("outbox relay could not flush to Kafka")
            return 0
        self._store.mark_published([row["id"] for row in rows])
        return len(rows)


def _least_loaded(workers: list, zone: str, load: dict):
    available = [w for w in workers if (not w.zones or zone in w.zones) and load[w.worker_id] < w.slots]
    # The random tie-break matters. A plain min() always takes the first listed worker
    # on a tie, so under light load one pod would do everything and the rest sit idle.
    return min(available, key=lambda w: (load[w.worker_id], random.random()), default=None)
