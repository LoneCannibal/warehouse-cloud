"""Tests the function against in-memory Cloud Storage and Firestore, with a stub Kafka."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kafka_auth  # noqa: E402
import local_runner  # noqa: E402
import main  # noqa: E402
from warehouse_common import kafka as shared_kafka  # noqa: E402
from warehouse_common.fakes import Firestore, Storage  # noqa: E402

BUCKET = "warehouse-data"
NAME = "manifests/o1/pkg-o1.json"
MANIFEST = {"order_id": "o1", "package_id": "pkg-o1", "box": "SMALL", "weight_kg": 0.4, "lines": []}


class StubProducer:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def produce(self, topic, key, value, on_delivery):
        self.sent.append(json.loads(value))
        on_delivery("broker down" if self.fail else None, None)

    def flush(self, _timeout):
        return 0


class Request:
    def __init__(self, body):
        self._body = body

    def get_json(self, silent=False):
        return self._body


def push(name=NAME, event_type="OBJECT_FINALIZE", message_id="m1"):
    """The body Pub/Sub sends for a Cloud Storage notification."""
    return {
        "message": {
            "attributes": {"eventType": event_type, "bucketId": BUCKET, "objectId": name},
            "data": "",
            "messageId": message_id,
        },
        "subscription": "projects/p/subscriptions/warehouse-dispatch",
    }


@pytest.fixture
def cloud(monkeypatch):
    storage, firestore = Storage(), Firestore()
    storage.bucket(BUCKET).blob(NAME).upload_from_string(json.dumps(MANIFEST))
    monkeypatch.setattr(main, "_storage", storage)
    monkeypatch.setattr(main, "_shipments", firestore.collection("shipments"))
    monkeypatch.setattr(main, "_producer", StubProducer())
    return SimpleNamespace(bucket=storage.bucket(BUCKET), shipments=firestore.collection("shipments"))


def shipment_count(cloud) -> int:
    return len(list(cloud.shipments.stream()))


def test_manifest_becomes_a_shipment_a_label_and_an_event(cloud):
    body, status = main.dispatch(Request(push()))

    assert status == 204
    assert shipment_count(cloud) == 1
    label = json.loads(cloud.bucket.blob("labels/o1/pkg-o1.json").download_as_bytes())
    assert label["tracking_id"].startswith("TRK-")
    event = main._producer.sent[0]
    assert event["event_type"] == "SHIPMENT_DISPATCHED"
    assert event["zone"] == "D" and event["order_id"] == "o1"


def test_a_duplicate_delivery_ships_nothing_twice(cloud):
    main.dispatch(Request(push()))
    main.dispatch(Request(push()))

    assert shipment_count(cloud) == 1
    assert len(main._producer.sent) == 1


@pytest.mark.parametrize(
    "envelope",
    [push(event_type="OBJECT_DELETE"), push(name="labels/o1/pkg-o1.json"), {}, {"message": {}}],
)
def test_other_notifications_are_acknowledged_and_ignored(cloud, envelope):
    _, status = main.dispatch(Request(envelope))

    assert status == 204
    assert shipment_count(cloud) == 0


def test_kafka_failure_leaves_no_shipment_so_the_retry_can_publish(cloud, monkeypatch):
    monkeypatch.setattr(main, "_producer", StubProducer(fail=True))

    _, status = main.dispatch(Request(push()))

    assert status == 500
    assert shipment_count(cloud) == 0

    monkeypatch.setattr(main, "_producer", StubProducer())
    assert main.dispatch(Request(push()))[1] == 204
    assert shipment_count(cloud) == 1


def test_a_missing_manifest_asks_pub_sub_to_retry(cloud):
    _, status = main.dispatch(Request(push(name="manifests/missing.json")))

    assert status == 500


def test_local_runner_builds_the_push_envelope_and_acks_only_on_success(cloud):
    class Message:
        attributes = {"eventType": "OBJECT_FINALIZE", "bucketId": BUCKET, "objectId": NAME}
        data = b"{}"
        message_id = "x"

        def __init__(self):
            self.outcome = None

        def ack(self):
            self.outcome = "ack"

        def nack(self):
            self.outcome = "nack"

    envelope = local_runner.to_envelope(Message())
    assert main.manifest_location(envelope) == (BUCKET, NAME)

    good = Message()
    local_runner.on_message(good)
    assert good.outcome == "ack"

    bad = Message()
    bad.attributes = {**Message.attributes, "objectId": "manifests/missing.json"}
    local_runner.on_message(bad)
    assert bad.outcome == "nack"


def test_the_function_builds_the_same_kafka_token_as_the_services(monkeypatch):
    monkeypatch.delenv("GOOGLE_MANAGED_KAFKA_AUTH_PRINCIPAL", raising=False)
    credentials = SimpleNamespace(
        token="ya29.token",
        expiry=datetime(2030, 1, 1) + timedelta(hours=1),
        service_account_email="dispatch@demo.iam.gserviceaccount.com",
        valid=True,
    )
    monkeypatch.setattr(kafka_auth.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(shared_kafka.time, "time", lambda: 1_000.0)

    ours = kafka_auth.GoogleTokenProvider(credentials)("")
    theirs = shared_kafka.GoogleTokenProvider(credentials)("")

    assert ours == theirs
