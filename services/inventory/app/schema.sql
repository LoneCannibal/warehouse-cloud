CREATE SCHEMA IF NOT EXISTS inventory;

CREATE TABLE IF NOT EXISTS inventory.stock (
    sku       text PRIMARY KEY,
    name      text NOT NULL,
    zone      text NOT NULL,
    bin       text NOT NULL,
    on_hand   integer NOT NULL CHECK (on_hand >= 0),
    reserved  integer NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    CHECK (reserved <= on_hand)
);

CREATE TABLE IF NOT EXISTS inventory.reservations (
    order_id  text NOT NULL,
    sku       text NOT NULL REFERENCES inventory.stock (sku),
    quantity  integer NOT NULL,
    status    text NOT NULL DEFAULT 'HELD',
    PRIMARY KEY (order_id, sku)
);

-- One row per confirmed pick task, so a reclaimed task cannot decrement stock twice.
CREATE TABLE IF NOT EXISTS inventory.picks (
    task_id       text PRIMARY KEY,
    confirmed_at  timestamptz NOT NULL DEFAULT now()
);
