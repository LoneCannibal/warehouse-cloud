CREATE SCHEMA IF NOT EXISTS scheduler;

CREATE TABLE IF NOT EXISTS scheduler.orders (
    order_id       text PRIMARY KEY,
    priority       integer NOT NULL DEFAULT 0,
    lines          jsonb NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    dispatched_at  timestamptz
);

-- The tasks table is the work queue: PENDING rows wait for the leader to assign them.
CREATE TABLE IF NOT EXISTS scheduler.tasks (
    task_id       text PRIMARY KEY,
    order_id      text NOT NULL REFERENCES scheduler.orders (order_id),
    task_type     text NOT NULL,
    zone          text NOT NULL,
    lines         jsonb NOT NULL,
    priority      integer NOT NULL DEFAULT 0,
    status        text NOT NULL DEFAULT 'PENDING',
    worker_id     text,
    attempt       integer NOT NULL DEFAULT 1,
    created_at    timestamptz NOT NULL DEFAULT now(),
    assigned_at   timestamptz,
    abandoned_at  timestamptz,
    completed_at  timestamptz
);

CREATE INDEX IF NOT EXISTS tasks_queue_idx ON scheduler.tasks (status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS tasks_order_idx ON scheduler.tasks (order_id);

-- Events are written here in the same transaction as the state change and relayed
-- to Kafka by the leader, so a crash between the two cannot lose an event.
CREATE TABLE IF NOT EXISTS scheduler.outbox (
    id       bigserial PRIMARY KEY,
    topic    text NOT NULL,
    key      text NOT NULL,
    payload  jsonb NOT NULL
);

-- The newest leader epoch. A stale leader's assignments stop matching this row.
CREATE TABLE IF NOT EXISTS scheduler.leader (
    id     integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    epoch  bigint NOT NULL
);

INSERT INTO scheduler.leader (id, epoch) VALUES (1, 0) ON CONFLICT DO NOTHING;
