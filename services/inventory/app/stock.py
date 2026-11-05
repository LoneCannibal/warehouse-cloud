"""Stock operations. Atomicity comes from conditional row updates, not from locks."""
from __future__ import annotations

from psycopg_pool import ConnectionPool

ZONES = ("A", "B", "C")
SKUS_PER_ZONE = 100
SEED_ON_HAND = 1_000_000


def seed(pool: ConnectionPool) -> None:
    rows = [
        (f"SKU-{zone}{n:03d}", f"Item {zone}{n:03d}", zone, f"{zone}-{n % 10:02d}-{n % 7:02d}", SEED_ON_HAND)
        for zone in ZONES
        for n in range(SKUS_PER_ZONE)
    ]
    with pool.connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO inventory.stock (sku, name, zone, bin, on_hand)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sku) DO NOTHING
            """,
            rows,
        )


def reserve(pool: ConnectionPool, order_id: str, lines: list[tuple[str, int]]) -> tuple[bool, str, list[dict]]:
    """Reserves every line or none. Returns (reserved, reason, allocations)."""
    with pool.connection() as conn:
        existing = conn.execute(
            """
            SELECT r.sku, r.quantity, s.zone, s.bin
            FROM inventory.reservations r JOIN inventory.stock s USING (sku)
            WHERE r.order_id = %s AND r.status = 'HELD'
            """,
            (order_id,),
        ).fetchall()
        if existing:
            return True, "", existing

        allocations = []
        # Sorted so two orders sharing SKUs lock rows in the same order and cannot deadlock.
        for sku, quantity in sorted(lines):
            row = conn.execute(
                """
                UPDATE inventory.stock
                SET reserved = reserved + %(quantity)s
                WHERE sku = %(sku)s AND on_hand - reserved >= %(quantity)s
                RETURNING zone, bin
                """,
                {"sku": sku, "quantity": quantity},
            ).fetchone()
            if row is None:
                conn.rollback()
                return False, f"insufficient stock for {sku}", []
            conn.execute(
                "INSERT INTO inventory.reservations (order_id, sku, quantity) VALUES (%s, %s, %s)",
                (order_id, sku, quantity),
            )
            allocations.append({"sku": sku, "quantity": quantity, "zone": row["zone"], "bin": row["bin"]})
        return True, "", allocations


def release(pool: ConnectionPool, order_id: str) -> bool:
    with pool.connection() as conn:
        held = conn.execute(
            """
            UPDATE inventory.reservations SET status = 'RELEASED'
            WHERE order_id = %s AND status = 'HELD'
            RETURNING sku, quantity
            """,
            (order_id,),
        ).fetchall()
        for row in held:
            conn.execute(
                "UPDATE inventory.stock SET reserved = reserved - %s WHERE sku = %s",
                (row["quantity"], row["sku"]),
            )
        return bool(held)


def confirm_pick(pool: ConnectionPool, task_id: str, lines: list[tuple[str, int]]) -> None:
    """Moves reserved units out of stock once per task_id, however often it is retried."""
    with pool.connection() as conn:
        first_time = conn.execute(
            "INSERT INTO inventory.picks (task_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING task_id",
            (task_id,),
        ).fetchone()
        if first_time is None:
            return
        for sku, quantity in sorted(lines):
            conn.execute(
                """
                UPDATE inventory.stock
                SET on_hand = on_hand - %(quantity)s, reserved = reserved - %(quantity)s
                WHERE sku = %(sku)s
                """,
                {"sku": sku, "quantity": quantity},
            )


def get_stock(pool: ConnectionPool, sku: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT sku, name, zone, bin, on_hand, reserved FROM inventory.stock WHERE sku = %s",
            (sku,),
        ).fetchone()
