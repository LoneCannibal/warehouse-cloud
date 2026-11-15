CREATE SCHEMA IF NOT EXISTS orders;

CREATE TABLE IF NOT EXISTS orders.orders (
    order_id         text PRIMARY KEY,
    idempotency_key  text UNIQUE,
    customer         text NOT NULL,
    priority         integer NOT NULL DEFAULT 0,
    lines            jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- The Warehouse Console lists the newest orders first.
CREATE INDEX IF NOT EXISTS orders_created_idx ON orders.orders (created_at DESC);
