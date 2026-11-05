"""The ZooKeeper behaviour the demo relies on, run against a real ensemble.

Each test maps to a row of the failure table in docs/design-document.md.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from app.leader import Leader, _least_loaded
from conftest import InProcessScheduler, RecordingProducer
from warehouse_common import events, zk
from warehouse_common.rpc import workers_pb2
from warehouse_common.worker import TaskWorker

CRASH_WORKER = Path(__file__).with_name("crash_worker.py")


def wait_for(condition, timeout=20.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def order(order_id, zones=("A",)):
    return {
        "order_id": order_id,
        "priority": 0,
        "lines": [{"sku": f"SKU-{z}001", "quantity": 1, "zone": z, "bin": f"{z}-01-01"} for z in zones],
    }


class StaticRegistry:
    """Answers ListAvailable from ZooKeeper's live group, as the real registry does."""

    def __init__(self, client) -> None:
        self._client = client

    def ListAvailable(self, request, timeout=None):
        names = self._client.get_children(f"{zk.WORKERS}/{request.capability}")
        return workers_pb2.ListAvailableResponse(
            workers=[
                workers_pb2.Worker(worker_id=n, capability=request.capability, slots=2) for n in names
            ]
        )


def make_leader(settings, client, store, epoch=1):
    leader = Leader(settings, client, store, RecordingProducer(), StaticRegistry(client))
    leader._epoch = epoch
    store.set_epoch(epoch)
    return leader


def make_worker(settings, client, store, name, process, capability="picking"):
    worker = TaskWorker(
        replace(settings, worker_id=name),
        client,
        capability=capability,
        zones=[],
        process=process,
        scheduler=InProcessScheduler(store),
        producer=RecordingProducer(),
    )
    worker.start()
    return worker


def task_status(store, task_id):
    with store._pool.connection() as conn:
        return conn.execute(
            "SELECT status, attempt, worker_id FROM scheduler.tasks WHERE task_id = %s", (task_id,)
        ).fetchone()


def outbox_types(store):
    with store._pool.connection() as conn:
        rows = conn.execute("SELECT payload FROM scheduler.outbox ORDER BY id").fetchall()
    return [row["payload"]["event_type"] for row in rows]


# ---- Leader election ---------------------------------------------------------


class Candidate:
    def __init__(self, client, name) -> None:
        self.client = client
        self.epochs = []
        self.election = zk.LeaderElection(client, name, self.epochs.append, lambda: None)
        self.election.start()


def test_one_leader_and_failover_raises_the_epoch(clients):
    first = Candidate(clients(), "first")
    wait_for(lambda: first.election.leading)
    second = Candidate(clients(), "second")
    third = Candidate(clients(), "third")
    time.sleep(0.5)
    assert not second.election.leading and not third.election.leading

    first.client.stop()

    wait_for(lambda: second.election.leading)
    assert not third.election.leading
    assert second.epochs[0] > first.epochs[0]


def test_losing_a_follower_changes_nothing_for_the_leader(clients):
    first = Candidate(clients(), "first")
    wait_for(lambda: first.election.leading)
    second = Candidate(clients(), "second")
    third = Candidate(clients(), "third")
    time.sleep(0.5)

    second.client.stop()
    time.sleep(0.5)

    assert first.election.leading and not third.election.leading
    first.client.stop()
    wait_for(lambda: third.election.leading)


def test_a_stale_leader_cannot_assign(settings, clients, store):
    client = clients()
    store.create_order(order("o1"))
    stale = make_leader(settings, client, store, epoch=1)
    store.set_epoch(2)  # A newer leader was elected while this one was paused.

    make_worker(settings, clients(), store, "w1", lambda task: None)
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))

    assert stale.assign_once() == 0
    assert task_status(store, "o1.PICK.A")["status"] == "PENDING"


# ---- Worker failure ----------------------------------------------------------


def test_killed_worker_is_detected_by_session_timeout_and_its_task_reclaimed(settings, clients, store):
    client = clients()
    leader = make_leader(settings, client, store)
    store.create_order(order("o1"))

    doomed = subprocess.Popen(
        [sys.executable, str(CRASH_WORKER), "doomed", settings.zk_hosts, str(settings.zk_session_timeout)],
        stdout=subprocess.PIPE,
    )
    try:
        assert doomed.stdout.readline().strip() == b"ready"
        wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))
        assert leader.assign_once() == 1
        wait_for(lambda: client.exists(f"{zk.LEASES}/o1.PICK.A"))
    finally:
        doomed.kill()
        doomed.wait()
    killed_at = time.time()

    # The session is still open on the server, so the lease survives the process briefly.
    assert client.exists(f"{zk.LEASES}/o1.PICK.A")
    wait_for(lambda: not client.exists(f"{zk.LEASES}/o1.PICK.A"))
    assert time.time() - killed_at > 1
    wait_for(lambda: not client.get_children(f"{zk.WORKERS}/picking"))

    wait_for(lambda: leader.reap_once() == 1, timeout=10, interval=0.2)
    row = task_status(store, "o1.PICK.A")
    assert row["status"] == "PENDING" and row["attempt"] == 2 and row["worker_id"] is None

    make_worker(settings, clients(), store, "rescuer", lambda task: None)
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))
    wait_for(lambda: leader.assign_once() == 1)
    wait_for(lambda: task_status(store, "o1.PICK.A")["status"] == "DONE")

    assert task_status(store, "o1.PICK.A")["worker_id"] == "rescuer"
    emitted = outbox_types(store)
    assert events.TASK_ABANDONED in emitted and events.TASK_RECLAIMED in emitted


def test_hung_worker_loses_its_lease_and_its_late_result_is_fenced(settings, clients, store):
    client = clients()
    client.set(zk.LEASE_TTL, b"1")
    leader = make_leader(settings, client, store)
    store.create_order(order("o1"))

    entered = threading.Event()

    def process(_task):
        entered.set()

    hung = make_worker(settings, clients(), store, "hung", process)
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))
    hung.hang()
    assert leader.assign_once() == 1
    assert entered.wait(10)

    # The session stays healthy, so only expires_at can reveal this worker.
    assert client.get_children(f"{zk.WORKERS}/picking") == ["hung"]
    wait_for(lambda: client.exists(f"{zk.LEASES}/o1.PICK.A"))
    wait_for(lambda: leader.reap_once() == 1, timeout=10, interval=0.2)
    assert task_status(store, "o1.PICK.A")["attempt"] == 2

    make_worker(settings, clients(), store, "rescuer", lambda task: None)
    wait_for(lambda: len(client.get_children(f"{zk.WORKERS}/picking")) == 2)

    # The least-loaded choice can fall on either worker, so keep the hung one busy.
    def assigned_to_rescuer():
        leader.assign_once()
        row = task_status(store, "o1.PICK.A")
        if row["worker_id"] == "hung":
            leader.reap_once()
        return task_status(store, "o1.PICK.A")["status"] == "DONE"

    wait_for(assigned_to_rescuer, timeout=15, interval=0.2)

    with_stale_attempt = store.complete("o1.PICK.A", "hung", 1)
    assert with_stale_attempt[0] is False
    hung.resume()
    assert task_status(store, "o1.PICK.A")["worker_id"] == "rescuer"


def test_new_worker_joins_and_gets_work_without_any_scheduler_change(settings, clients, store):
    client = clients()
    leader = make_leader(settings, client, store)
    store.create_order(order("o1"))
    assert leader.assign_once() == 0

    make_worker(settings, clients(), store, "scaled-out", lambda task: None)
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))

    assert leader.assign_once() == 1
    wait_for(lambda: task_status(store, "o1.PICK.A")["status"] == "DONE")


def test_lease_ttl_change_reaches_a_running_worker(settings, clients, store):
    client = clients()
    worker = make_worker(settings, clients(), store, "w1", lambda task: None)

    client.set(zk.LEASE_TTL, b"7")

    wait_for(lambda: worker._lease_ttl == 7.0)


def test_a_reclaimed_task_goes_ahead_of_a_higher_priority_backlog(settings, clients, store):
    client = clients()
    make_leader(settings, client, store)
    store.create_order({**order("old"), "priority": 0})
    make_worker(settings, clients(), store, "w1", lambda task: None)
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))
    task = store.pending(1)[0]
    store.assign(task, "w1", 1)
    store.abandon({**task, "worker_id": "w1"})
    store.create_order({**order("urgent"), "priority": 9})

    assert [row["order_id"] for row in store.pending(10)] == ["old", "urgent"]


def test_idle_workers_share_the_work():
    workers = [workers_pb2.Worker(worker_id=name, slots=2) for name in ("a", "b", "c")]
    chosen = {_least_loaded(workers, "A", defaultdict(int)).worker_id for _ in range(100)}
    assert chosen == {"a", "b", "c"}


def test_the_least_loaded_worker_wins_and_a_full_worker_is_skipped():
    workers = [workers_pb2.Worker(worker_id=name, slots=2) for name in ("a", "b", "c")]
    load = defaultdict(int, {"a": 2, "b": 1, "c": 0})
    assert _least_loaded(workers, "A", load).worker_id == "c"
    assert _least_loaded(workers[:1], "A", load) is None


def test_workers_are_matched_by_zone():
    workers = [workers_pb2.Worker(worker_id="a", slots=2, zones=["B"])]
    assert _least_loaded(workers, "A", defaultdict(int)) is None
    assert _least_loaded(workers, "B", defaultdict(int)).worker_id == "a"


# ---- Order flow --------------------------------------------------------------


def test_redelivered_order_creates_each_task_once(store):
    event = order("o1", zones=("A", "B"))
    store.create_order(event)
    store.create_order(event)

    assert outbox_types(store).count(events.TASK_CREATED) == 2


def test_two_final_picks_finishing_together_create_exactly_one_pack(settings, clients, store):
    client = clients()
    leader = make_leader(settings, client, store)
    store.create_order(order("o1", zones=("A", "B")))
    finish = threading.Event()
    make_worker(settings, clients(), store, "w1", lambda task: finish.wait())
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))
    assert leader.assign_once() == 2

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda task_id: store.complete(task_id, "w1", 1), ["o1.PICK.A", "o1.PICK.B"])
        )

    finish.set()
    assert all(accepted for accepted, _ in results)
    with store._pool.connection() as conn:
        packs = conn.execute("SELECT count(*) AS n FROM scheduler.tasks WHERE task_type = 'PACK'").fetchone()
    assert packs["n"] == 1


def test_duplicate_completion_is_rejected(settings, clients, store):
    client = clients()
    leader = make_leader(settings, client, store)
    store.create_order(order("o1"))
    make_worker(settings, clients(), store, "w1", lambda task: None)
    wait_for(lambda: client.get_children(f"{zk.WORKERS}/picking"))
    leader.assign_once()
    wait_for(lambda: task_status(store, "o1.PICK.A")["status"] == "DONE")

    accepted, reason = store.complete("o1.PICK.A", "w1", 1)

    assert accepted is False and "no longer owns" in reason


def test_outbox_relay_publishes_then_clears(settings, clients, store):
    leader = make_leader(settings, clients(), store)
    store.create_order(order("o1"))

    assert leader.relay_once() == 1
    assert leader.relay_once() == 0
    assert leader._producer.sent[0][0] == events.TOPIC_TASK_EVENTS
