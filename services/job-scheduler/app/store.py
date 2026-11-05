"""Task state in PostgreSQL.

Two fencing checks live here, because the database is the one place that a stale
leader or a stale worker cannot get around:
  * assign() requires the caller's epoch to match the newest leader epoch.
  * complete() requires the caller's attempt to match the task's current attempt.
"""
from __future__ import annotations

from datetime import datetime, timezone

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from warehouse_common import events

PACK_ZONE = "P"


def _emit(conn: Connection, event: dict) -> None:
    conn.execute(
        "INSERT INTO scheduler.outbox (topic, key, payload) VALUES (%s, %s, %s)",
        (events.TOPIC_TASK_EVENTS, event["order_id"], Jsonb(event)),
    )


def _create_task(
    conn: Connection, task_id: str, order_id: str, task_type: str, zone: str, lines: list, priority: int
) -> None:
    created = conn.execute(
        """
        INSERT INTO scheduler.tasks (task_id, order_id, task_type, zone, lines, priority)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (task_id) DO NOTHING
        RETURNING task_id
        """,
        (task_id, order_id, task_type, zone, Jsonb(lines), priority),
    ).fetchone()
    if created:
        _emit(conn, events.task_event(events.TASK_CREATED, task_id, order_id, task_type, zone, attempt=1))


class TaskStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def set_epoch(self, epoch: int) -> None:
        with self._pool.connection() as conn:
            conn.execute("UPDATE scheduler.leader SET epoch = %(epoch)s WHERE epoch < %(epoch)s", {"epoch": epoch})

    def create_order(self, event: dict) -> None:
        """Creates one PICK task per zone. Task ids are deterministic, so redelivery is harmless."""
        order_id = event["order_id"]
        priority = event["priority"]
        by_zone: dict[str, list] = {}
        for line in event["lines"]:
            by_zone.setdefault(line["zone"], []).append(line)

        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO scheduler.orders (order_id, priority, lines)
                VALUES (%s, %s, %s)
                ON CONFLICT (order_id) DO NOTHING
                """,
                (order_id, priority, Jsonb(event["lines"])),
            )
            for zone, lines in by_zone.items():
                _create_task(conn, f"{order_id}.PICK.{zone}", order_id, "PICK", zone, lines, priority)

    def pending(self, limit: int) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                SELECT task_id, order_id, task_type, zone, attempt, abandoned_at
                FROM scheduler.tasks
                WHERE status = 'PENDING'
                -- A reclaimed task goes first. Its order already lost time to a failure,
                -- and behind a backlog its recovery time would be queueing, not recovery.
                ORDER BY abandoned_at IS NULL, priority DESC, created_at
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def in_flight(self) -> dict[str, int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT worker_id, count(*) AS n FROM scheduler.tasks WHERE status = 'ASSIGNED' GROUP BY worker_id"
            ).fetchall()
        return {row["worker_id"]: row["n"] for row in rows}

    def active(self) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                """
                SELECT task_id, order_id, task_type, zone, worker_id, attempt, assigned_at
                FROM scheduler.tasks
                WHERE status = 'ASSIGNED'
                """
            ).fetchall()

    def assign(self, task: dict, worker_id: str, epoch: int) -> int | None:
        """Returns the recovery time in ms for a reclaimed task, 0 otherwise, or None if refused."""
        with self._pool.connection() as conn:
            assigned = conn.execute(
                """
                UPDATE scheduler.tasks
                SET status = 'ASSIGNED', worker_id = %(worker)s, assigned_at = now(), abandoned_at = NULL
                WHERE task_id = %(task)s AND status = 'PENDING'
                  AND (SELECT epoch FROM scheduler.leader) = %(epoch)s
                RETURNING assigned_at
                """,
                {"worker": worker_id, "task": task["task_id"], "epoch": epoch},
            ).fetchone()
            if assigned is None:
                return None

            identity = (task["task_id"], task["order_id"], task["task_type"], task["zone"])
            _emit(conn, events.task_event(events.TASK_ASSIGNED, *identity, worker_id, task["attempt"]))

            recovery_ms = 0
            if task["abandoned_at"] is not None:
                recovery_ms = int((assigned["assigned_at"] - task["abandoned_at"]).total_seconds() * 1000)
                _emit(
                    conn,
                    events.task_event(
                        events.TASK_RECLAIMED, *identity, worker_id, task["attempt"], max(recovery_ms, 0)
                    ),
                )
            return recovery_ms

    def abandon(self, task: dict) -> bool:
        """Requeues a task under a new attempt number, which fences off its old worker."""
        with self._pool.connection() as conn:
            requeued = conn.execute(
                """
                UPDATE scheduler.tasks
                SET status = 'PENDING', worker_id = NULL, assigned_at = NULL,
                    attempt = attempt + 1, abandoned_at = now()
                WHERE task_id = %s AND attempt = %s AND status = 'ASSIGNED'
                RETURNING task_id
                """,
                (task["task_id"], task["attempt"]),
            ).fetchone()
            if requeued is None:
                return False
            _emit(
                conn,
                events.task_event(
                    events.TASK_ABANDONED,
                    task["task_id"],
                    task["order_id"],
                    task["task_type"],
                    task["zone"],
                    task["worker_id"],
                    task["attempt"],
                ),
            )
            return True

    def get(self, task_id: str) -> dict | None:
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT task_id, order_id, task_type, zone, attempt, lines FROM scheduler.tasks WHERE task_id = %s",
                (task_id,),
            ).fetchone()

    def complete(self, task_id: str, worker_id: str, attempt: int) -> tuple[bool, str]:
        """Marks a task done if the caller still owns this attempt.

        When the last PICK of an order completes, this also creates its PACK task
        in the same transaction. The order row is locked first: without that, two
        final picks finishing together could each see the other still open, and
        neither would create the PACK task.
        """
        with self._pool.connection() as conn:
            task = conn.execute(
                "SELECT order_id, task_type, zone FROM scheduler.tasks WHERE task_id = %s", (task_id,)
            ).fetchone()
            if task is None:
                return False, "unknown task"

            order = conn.execute(
                "SELECT lines, priority FROM scheduler.orders WHERE order_id = %s FOR UPDATE",
                (task["order_id"],),
            ).fetchone()

            done = conn.execute(
                """
                UPDATE scheduler.tasks
                SET status = 'DONE', completed_at = now()
                WHERE task_id = %s AND worker_id = %s AND attempt = %s AND status = 'ASSIGNED'
                RETURNING task_id
                """,
                (task_id, worker_id, attempt),
            ).fetchone()
            if done is None:
                return False, f"attempt {attempt} no longer owns this task"

            order_id = task["order_id"]
            _emit(
                conn,
                events.task_event(
                    events.TASK_COMPLETED, task_id, order_id, task["task_type"], task["zone"], worker_id, attempt
                ),
            )

            if task["task_type"] == "PICK":
                open_picks = conn.execute(
                    """
                    SELECT count(*) AS n FROM scheduler.tasks
                    WHERE order_id = %s AND task_type = 'PICK' AND status <> 'DONE'
                    """,
                    (order_id,),
                ).fetchone()
                if open_picks["n"] == 0:
                    _create_task(
                        conn, f"{order_id}.PACK", order_id, "PACK", PACK_ZONE, order["lines"], order["priority"]
                    )
            return True, ""

    def mark_dispatched(self, order_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE scheduler.orders SET dispatched_at = %s WHERE order_id = %s AND dispatched_at IS NULL",
                (datetime.now(timezone.utc), order_id),
            )

    def order_view(self, order_id: str) -> dict | None:
        with self._pool.connection() as conn:
            order = conn.execute(
                "SELECT order_id, dispatched_at FROM scheduler.orders WHERE order_id = %s", (order_id,)
            ).fetchone()
            if order is None:
                return None
            tasks = conn.execute(
                """
                SELECT task_id, task_type, zone, status, worker_id, attempt
                FROM scheduler.tasks WHERE order_id = %s ORDER BY created_at
                """,
                (order_id,),
            ).fetchall()
        return {"order_id": order_id, "status": _order_status(order, tasks), "tasks": tasks}

    def queue_depth(self) -> dict[str, int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT task_type, count(*) AS n FROM scheduler.tasks WHERE status = 'PENDING' GROUP BY task_type"
            ).fetchall()
        return {row["task_type"]: row["n"] for row in rows}

    def unpublished(self, limit: int) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT id, topic, key, payload FROM scheduler.outbox ORDER BY id LIMIT %s", (limit,)
            ).fetchall()

    def mark_published(self, ids: list[int]) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM scheduler.outbox WHERE id = ANY(%s)", (ids,))


def _order_status(order: dict, tasks: list[dict]) -> str:
    if order["dispatched_at"] is not None:
        return "DISPATCHED"
    packs = [t for t in tasks if t["task_type"] == "PACK"]
    if packs:
        return "PACKED" if packs[0]["status"] == "DONE" else "PACKING"
    if any(t["status"] in ("ASSIGNED", "DONE") for t in tasks):
        return "PICKING"
    return "ACCEPTED"
