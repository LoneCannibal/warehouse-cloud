"""Thin wrappers over confluent-kafka.

Delivery is at-least-once everywhere, so every consumer must be idempotent.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from datetime import timezone
from typing import Callable

from confluent_kafka import Consumer, KafkaError, Producer

from warehouse_common.metrics import EVENTS_CONSUMED, EVENTS_PRODUCED
from warehouse_common.settings import Settings

log = logging.getLogger(__name__)

HANDLER_ATTEMPTS = 5


def client_config(settings: Settings) -> dict:
    if settings.kafka_auth == "google":
        # Managed Service for Apache Kafka accepts only TLS with SASL, never plaintext.
        config = {
            "bootstrap.servers": settings.kafka_bootstrap,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "OAUTHBEARER",
            "oauth_cb": GoogleTokenProvider(),
        }
    else:
        config = {
            "bootstrap.servers": settings.kafka_bootstrap,
            "security.protocol": settings.kafka_security_protocol,
        }
    if config["security.protocol"] in ("SSL", "SASL_SSL"):
        # The wheel bundles OpenSSL, so tell it to look for the system's CA certificates.
        config["ssl.ca.location"] = "probe"
    return config


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class GoogleTokenProvider:
    """The OAUTHBEARER token that Managed Service for Apache Kafka expects.

    The broker takes a Google access token wrapped in a JWT-shaped envelope that names
    the caller. This follows Google's sample in github.com/googleapis/managedkafka.
    On GKE the credentials come from Workload Identity, so no key file is involved.
    """

    HEADER = json.dumps({"typ": "JWT", "alg": "GOOG_OAUTH2_TOKEN"})

    def __init__(self, credentials=None) -> None:
        self._credentials = credentials
        self._lock = threading.Lock()

    def __call__(self, _config: str) -> tuple[str, float]:
        # librdkafka calls this from its own thread, and again before each token expires.
        with self._lock:
            credentials = self._valid_credentials()
            expiry = credentials.expiry.replace(tzinfo=timezone.utc).timestamp()
            subject = os.environ.get("GOOGLE_MANAGED_KAFKA_AUTH_PRINCIPAL") or getattr(
                credentials, "service_account_email", None
            )
            if not subject:
                raise ValueError("cannot tell who these credentials belong to; set GOOGLE_MANAGED_KAFKA_AUTH_PRINCIPAL")
            claims = json.dumps({"exp": expiry, "iss": "Google", "iat": time.time(), "sub": subject})
            return ".".join((_b64(self.HEADER), _b64(claims), _b64(credentials.token))), expiry

    def _valid_credentials(self):
        if self._credentials is None:
            import google.auth

            self._credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not self._credentials.valid:
            from google.auth.transport.requests import Request

            self._credentials.refresh(Request())
        return self._credentials


class EventProducer:
    def __init__(self, settings: Settings) -> None:
        self._producer = Producer(
            {
                **client_config(settings),
                "enable.idempotence": True,
                "linger.ms": 5,
            }
        )

    def send(self, topic: str, key: str, value: dict, on_delivery: Callable | None = None) -> None:
        payload = json.dumps(value)
        while True:
            try:
                self._producer.produce(topic, key=key, value=payload, on_delivery=on_delivery or self._report)
                break
            except BufferError:
                self._producer.poll(0.1)
        self._producer.poll(0)
        EVENTS_PRODUCED.labels(topic).inc()

    def flush(self, timeout: float = 10.0) -> int:
        """Returns the number of messages still undelivered after the timeout."""
        return self._producer.flush(timeout)

    @staticmethod
    def _report(error, message) -> None:
        if error is not None:
            log.error("kafka delivery failed: %s", error)


def consume(
    settings: Settings,
    group: str,
    topics: list[str],
    handler: Callable[[str, dict], None],
    stop: threading.Event,
) -> None:
    """Polls until stop is set. An offset is committed only after its handler succeeds."""
    consumer = Consumer(
        {
            **client_config(settings),
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            # The default is 5 minutes, so a topic created after this consumer
            # subscribed would sit unread for that long.
            "topic.metadata.refresh.interval.ms": 30000,
            # The default is 45 seconds. A killed scheduler leader keeps its
            # partitions until this expires, so the new leader would win the
            # election and then sit idle with orders queueing behind it.
            "session.timeout.ms": 10000,
            "heartbeat.interval.ms": 3000,
        }
    )
    consumer.subscribe(topics)
    try:
        while not stop.is_set():
            message = consumer.poll(0.5)
            if message is None:
                continue
            if message.error():
                if message.error().code() != KafkaError._PARTITION_EOF:
                    log.warning("kafka consumer error: %s", message.error())
                continue
            _handle(message, handler, stop)
            consumer.commit(message=message, asynchronous=True)
    finally:
        consumer.close()


def _handle(message, handler: Callable[[str, dict], None], stop: threading.Event) -> None:
    topic = message.topic()
    try:
        event = json.loads(message.value())
    except ValueError:
        log.error("dropping malformed event on %s", topic)
        return

    for attempt in range(1, HANDLER_ATTEMPTS + 1):
        try:
            handler(topic, event)
            EVENTS_CONSUMED.labels(topic).inc()
            return
        except Exception:
            log.exception("event handler failed, attempt %d of %d", attempt, HANDLER_ATTEMPTS)
            if stop.is_set():
                return
            time.sleep(attempt)
    log.error("giving up on event from %s after %d attempts", topic, HANDLER_ATTEMPTS)
