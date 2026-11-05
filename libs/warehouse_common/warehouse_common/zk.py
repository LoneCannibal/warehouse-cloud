"""ZooKeeper layout and leader election.

ZooKeeper holds coordination state only. Task payloads stay in PostgreSQL.

/warehouse
  scheduler/election/n_0000000012   ephemeral-sequential  one per Job Scheduler replica
  scheduler/epoch                   persistent            version is the leader epoch
  workers/<capability>/<worker>     ephemeral             live membership
  assignments/<worker>/<task>@<n>   persistent            leader offers attempt n of a task
  leases/<task>                     ephemeral             owned by the working worker's session
  config/lease_ttl_seconds          persistent            watched by workers, applies live
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from kazoo.client import KazooClient, KazooState
from kazoo.exceptions import KazooException, NodeExistsError
from kazoo.retry import KazooRetry

from warehouse_common.settings import Settings

log = logging.getLogger(__name__)

ROOT = "/warehouse"
ELECTION = f"{ROOT}/scheduler/election"
EPOCH = f"{ROOT}/scheduler/epoch"
WORKERS = f"{ROOT}/workers"
ASSIGNMENTS = f"{ROOT}/assignments"
LEASES = f"{ROOT}/leases"
LEASE_TTL = f"{ROOT}/config/lease_ttl_seconds"

CAPABILITIES = ("picking", "packing")
DEFAULT_LEASE_TTL_SECONDS = 20


def connect(settings: Settings) -> KazooClient:
    zk = KazooClient(
        hosts=settings.zk_hosts,
        timeout=settings.zk_session_timeout,
        connection_retry=KazooRetry(max_tries=-1, max_delay=5),
        # zk.retry() rides out a ZooKeeper server failing mid-call. Without it a
        # ConnectionLoss escapes, and a one-shot watch is never set again.
        command_retry=KazooRetry(max_tries=20, delay=0.2, max_delay=2),
    )
    zk.start(timeout=60)
    for path in (ELECTION, ASSIGNMENTS, LEASES, *(f"{WORKERS}/{c}" for c in CAPABILITIES)):
        zk.ensure_path(path)
    _ensure_value(zk, EPOCH, b"")
    _ensure_value(zk, LEASE_TTL, str(DEFAULT_LEASE_TTL_SECONDS).encode())
    return zk


def _ensure_value(zk: KazooClient, path: str, value: bytes) -> None:
    try:
        zk.create(path, value, makepath=True)
    except NodeExistsError:
        pass


class LeaderElection:
    """Leader election from ephemeral-sequential znodes.

    Each candidate creates one node. The lowest sequence number leads. Every other
    candidate watches only the node just before its own, so a leader failure wakes
    one successor instead of every candidate (no herd effect).

    `connected` is cleared while the session is SUSPENDED. A session in that state
    may already have expired on the server, so the leader must stop assigning work
    until it knows, otherwise two leaders could overlap.
    """

    def __init__(
        self,
        zk: KazooClient,
        identifier: str,
        on_elected: Callable[[int], None],
        on_revoked: Callable[[], None],
    ) -> None:
        self._zk = zk
        self._identifier = identifier
        self._on_elected = on_elected
        self._on_revoked = on_revoked
        self._node: str | None = None
        self._leading = False
        self._lock = threading.RLock()
        self.connected = threading.Event()
        self.connected.set()

    @property
    def leading(self) -> bool:
        return self._leading

    def start(self) -> None:
        self._zk.add_listener(self._on_state)
        self._zk.handler.spawn(self._join)

    def _on_state(self, state: str) -> None:
        # Runs on the connection thread, so no ZooKeeper calls can be made here.
        if state == KazooState.SUSPENDED:
            self.connected.clear()
        elif state == KazooState.LOST:
            self.connected.clear()
            self._zk.handler.spawn(self._revoke)
        elif state == KazooState.CONNECTED:
            self.connected.set()
            if self._node is None:
                self._zk.handler.spawn(self._join)

    def _join(self) -> None:
        try:
            with self._lock:
                if self._node is None:
                    self._node = self._zk.create(
                        f"{ELECTION}/n_", self._identifier.encode(), ephemeral=True, sequence=True
                    )
                    log.info("joined election as %s", self._node)
            self._check()
        except KazooException:
            # _on_state joins again when the connection returns.
            log.warning("could not join the election yet")

    def _check(self) -> None:
        with self._lock:
            if self._node is None:
                return
            name = self._node.rsplit("/", 1)[-1]
            candidates = sorted(self._zk.retry(self._zk.get_children, ELECTION))
            if name not in candidates:
                return
            position = candidates.index(name)
            if position == 0:
                self._become_leader()
                return
            predecessor = f"{ELECTION}/{candidates[position - 1]}"
            stat = self._zk.retry(self._zk.exists, predecessor, watch=self._on_predecessor_event)
            if stat is None:
                self._check()
            elif stat.ephemeralOwner == self._zk.client_id[0]:
                # A create whose reply was lost left a second node for this candidate.
                # Waiting on it would wait on ourselves forever and stall everyone behind.
                self._zk.retry(self._zk.delete, predecessor)

    def _on_predecessor_event(self, _event) -> None:
        self._zk.handler.spawn(self._recheck)

    def _recheck(self) -> None:
        try:
            self._check()
        except KazooException:
            log.warning("election check failed, rejoining on the next session")

    def _become_leader(self) -> None:
        if self._leading:
            return
        # Each new leader bumps the znode version, which gives a value that
        # only ever rises. It is the epoch the store uses to reject a stale leader.
        stat = self._zk.retry(self._zk.set, EPOCH, self._identifier.encode())
        self._leading = True
        log.info("elected leader", extra={"epoch": stat.version})
        self._on_elected(stat.version)

    def _revoke(self) -> None:
        with self._lock:
            self._node = None
            if self._leading:
                self._leading = False
                log.warning("leadership lost")
                self._on_revoked()
