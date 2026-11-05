CREATE SCHEMA IF NOT EXISTS workers;

-- Durable worker profiles. Live availability is mirrored to Firestore instead.
CREATE TABLE IF NOT EXISTS workers.profiles (
    worker_id   text PRIMARY KEY,
    capability  text NOT NULL,
    zones       text[] NOT NULL DEFAULT '{}',
    slots       integer NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now()
);
