"""Stands in for the Pub/Sub push subscription when there is no Google Cloud project.

Pulls Cloud Storage notifications from the Pub/Sub emulator and hands each one to
main.handle in the envelope shape a push delivery has. A message is acknowledged
only if the handler succeeds, so a failure comes back, as it would in the cloud. On
Google Cloud none of this file is used: infra/runtime/function.tf creates the push
subscription.
"""
from __future__ import annotations

import base64
import os
import sys

from google.cloud import pubsub_v1

import main

PROJECT = os.environ.get("GCP_PROJECT", "local-project")
SUBSCRIPTION = os.environ.get("DISPATCH_SUBSCRIPTION", "warehouse-dispatch")


def to_envelope(message) -> dict:
    return {
        "message": {
            "attributes": dict(message.attributes),
            "data": base64.b64encode(message.data).decode(),
            "messageId": message.message_id,
        },
        "subscription": SUBSCRIPTION,
    }


def on_message(message) -> None:
    try:
        main.handle(to_envelope(message))
    except Exception as error:
        print(f"manifest failed, will be redelivered: {error}", file=sys.stderr, flush=True)
        message.nack()
        return
    message.ack()


def run() -> None:
    subscriber = pubsub_v1.SubscriberClient()
    path = subscriber.subscription_path(PROJECT, SUBSCRIPTION)
    print(f"pulling {path}", file=sys.stderr, flush=True)
    future = subscriber.subscribe(path, callback=on_message, flow_control=pubsub_v1.types.FlowControl(max_messages=5))
    future.result()


if __name__ == "__main__":
    run()
