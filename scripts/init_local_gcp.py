"""Creates the Cloud Storage bucket and the Pub/Sub topic and subscription in the emulators.

This is the local counterpart of infra/runtime/storage.tf and function.tf. It uses
the same names, so the services run unchanged with or without Google Cloud. The
fake Cloud Storage server publishes a notification for each new manifest itself, so
only the topic it publishes to has to exist. Firestore needs no setup.
"""
from __future__ import annotations

import json
import os

from google.api_core.exceptions import AlreadyExists, Conflict
from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1, storage

PROJECT = os.environ.get("GCP_PROJECT", "local-project")
BUCKET = os.environ.get("DATA_BUCKET", "warehouse-data")
TOPIC = os.environ.get("MANIFESTS_TOPIC", "warehouse-manifests")
SUBSCRIPTION = os.environ.get("DISPATCH_SUBSCRIPTION", "warehouse-dispatch")


def main() -> None:
    try:
        storage.Client(project=PROJECT, credentials=AnonymousCredentials()).create_bucket(BUCKET)
    except Conflict:
        pass

    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(PROJECT, TOPIC)
    try:
        publisher.create_topic(name=topic)
    except AlreadyExists:
        pass

    subscriber = pubsub_v1.SubscriberClient()
    subscription = subscriber.subscription_path(PROJECT, SUBSCRIPTION)
    try:
        subscriber.create_subscription(name=subscription, topic=topic, ack_deadline_seconds=60)
    except AlreadyExists:
        pass

    print(json.dumps({"bucket": BUCKET, "topic": topic, "subscription": subscription}))


if __name__ == "__main__":
    main()
