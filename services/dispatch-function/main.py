"""Dispatch Service: records dispatch completion and shipment hand-off.

Flow: Packing writes a manifest to Cloud Storage, Cloud Storage notifies a Pub/Sub
topic, and a push subscription delivers each notification to this function. Any
reply other than 2xx makes Pub/Sub retry with backoff, and after five failed
deliveries the message moves to a dead-letter topic.

Delivery is at least once, so a manifest can arrive more than once. Firestore's
create() refuses a shipment that already exists, which makes the second delivery a
no-op, while the order of the steps below means a crash is simply retried.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import functions_framework
from google.api_core.exceptions import AlreadyExists

from kafka_auth import GoogleTokenProvider

logger = logging.getLogger(__name__)

TOPIC_TASK_EVENTS = "warehouse.task-events"
DISPATCH_ZONE = "D"
MANIFEST_PREFIX = "manifests/"
CARRIERS = ("BlueDart", "Delhivery", "Ecom Express", "DTDC")

# Created on first use and reused across requests to a warm instance. Tests swap in fakes.
_storage = None
_shipments = None
_producer = None


def storage():
    global _storage
    if _storage is None:
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import storage as gcs

        if os.environ.get("STORAGE_EMULATOR_HOST"):
            _storage = gcs.Client(project=os.environ["GCP_PROJECT"], credentials=AnonymousCredentials())
        else:
            _storage = gcs.Client(project=os.environ["GCP_PROJECT"])
    return _storage


def shipments():
    global _shipments
    if _shipments is None:
        from google.cloud import firestore

        client = firestore.Client(
            project=os.environ["GCP_PROJECT"], database=os.environ.get("FIRESTORE_DATABASE", "(default)")
        )
        _shipments = client.collection(os.environ.get("SHIPMENTS_COLLECTION", "shipments"))
    return _shipments


def producer():
    global _producer
    if _producer is None:
        from confluent_kafka import Producer

        config = {"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"], "enable.idempotence": True}
        if os.environ.get("KAFKA_AUTH") == "google":
            config.update(
                {
                    "security.protocol": "SASL_SSL",
                    "sasl.mechanisms": "OAUTHBEARER",
                    "oauth_cb": GoogleTokenProvider(),
                    "ssl.ca.location": "probe",
                }
            )
        _producer = Producer(config)
    return _producer


def log(message: str, **fields) -> None:
    # Cloud Logging reads severity and message from a JSON line on stdout.
    print(json.dumps({"severity": "INFO", "service": "dispatch", "message": message, **fields}), flush=True)


def manifest_location(envelope: dict) -> tuple[str, str] | None:
    """Reads the Cloud Storage notification out of a Pub/Sub push request."""
    attributes = (envelope.get("message") or {}).get("attributes") or {}
    if attributes.get("eventType") != "OBJECT_FINALIZE":
        return None
    name = attributes.get("objectId", "")
    if not name.startswith(MANIFEST_PREFIX):
        return None
    return attributes["bucketId"], name


def choose_carrier(package_id: str) -> str:
    return CARRIERS[sum(package_id.encode()) % len(CARRIERS)]


def publish_dispatched(manifest: dict, tracking_id: str) -> None:
    errors: list = []

    def on_delivery(error, _message) -> None:
        if error is not None:
            errors.append(error)

    event = {
        "event_id": uuid.uuid4().hex,
        "event_type": "SHIPMENT_DISPATCHED",
        "task_id": f"{manifest['order_id']}.DISPATCH",
        "order_id": manifest["order_id"],
        "task_type": "DISPATCH",
        "zone": DISPATCH_ZONE,
        "worker_id": None,
        "attempt": 1,
        "recovery_ms": None,
        "ts": int(time.time() * 1000),
    }
    kafka = producer()
    kafka.produce(TOPIC_TASK_EVENTS, key=manifest["order_id"], value=json.dumps(event), on_delivery=on_delivery)
    undelivered = kafka.flush(10)
    if undelivered or errors:
        raise RuntimeError(f"dispatch event not delivered: {errors or undelivered}")
    log("dispatch published", order_id=manifest["order_id"], tracking_id=tracking_id)


def process_manifest(bucket_name: str, object_name: str) -> None:
    bucket = storage().bucket(bucket_name)
    manifest = json.loads(bucket.blob(object_name).download_as_bytes())
    package_id = manifest["package_id"]
    order_id = manifest["order_id"]
    shipment = shipments().document(package_id)

    if shipment.get().exists:
        log("duplicate manifest ignored", order_id=order_id, package_id=package_id)
        return

    tracking_id = f"TRK-{uuid.uuid5(uuid.NAMESPACE_URL, package_id).hex[:12].upper()}"
    carrier = choose_carrier(package_id)
    dispatched_at = datetime.now(timezone.utc).isoformat()
    label = {
        "tracking_id": tracking_id,
        "carrier": carrier,
        "order_id": order_id,
        "package_id": package_id,
        "box": manifest["box"],
        "weight_kg": manifest["weight_kg"],
        "dispatched_at": dispatched_at,
    }
    bucket.blob(f"labels/{order_id}/{package_id}.json").upload_from_string(
        json.dumps(label), content_type="application/json"
    )

    # The event goes first. If the function dies here, nothing marks the package
    # as done, so the retry publishes again and the consumers absorb the duplicate.
    # Recording first would risk a shipment that no consumer ever hears about.
    publish_dispatched(manifest, tracking_id)

    try:
        shipment.create(
            {
                "package_id": package_id,
                "order_id": order_id,
                "tracking_id": tracking_id,
                "carrier": carrier,
                "status": "HANDED_OFF",
                "dispatched_at": dispatched_at,
            }
        )
    except AlreadyExists:
        log("shipment already recorded", order_id=order_id, package_id=package_id)


def handle(envelope: dict) -> None:
    """Processes one push delivery. Raising makes Pub/Sub deliver it again."""
    location = manifest_location(envelope)
    if location is None:
        return
    process_manifest(*location)


@functions_framework.http
def dispatch(request):
    envelope = request.get_json(silent=True) or {}
    try:
        handle(envelope)
    except Exception:
        message_id = (envelope.get("message") or {}).get("messageId")
        logger.exception("manifest failed: message %s", message_id)
        return ("retry later", 500)
    return ("", 204)
