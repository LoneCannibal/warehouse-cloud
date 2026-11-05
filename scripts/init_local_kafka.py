"""Creates the Kafka topics locally, as google_managed_kafka_topic does in infra/runtime/kafka.tf."""
from __future__ import annotations

import os

from confluent_kafka.admin import AdminClient, NewTopic

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPICS = {
    "warehouse.orders": 6,
    "warehouse.task-events": 6,
    "warehouse.analytics.results": 3,
}


def main() -> None:
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    existing = admin.list_topics(timeout=30).topics
    missing = [
        NewTopic(name, num_partitions=partitions, replication_factor=1)
        for name, partitions in TOPICS.items()
        if name not in existing
    ]
    if missing:
        for future in admin.create_topics(missing).values():
            future.result(timeout=30)
    print(f"topics ready: {', '.join(TOPICS)}")


if __name__ == "__main__":
    main()
