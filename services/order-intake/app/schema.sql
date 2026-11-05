CREATE SCHEMA IF NOT EXISTS orders;

CREATE TABLE IF NOT EXISTS orders.orders (
    order_id         text PRIMARY KEY,
    idempotency_key  text UNIQUE,
    customer         text NOT NULL,
    priority         integer NOT NULL DEFAULT 0,
    lines            jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
