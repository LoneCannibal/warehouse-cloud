"""The consumer settings that decide how long a failover stalls order flow, and the
token that Managed Service for Apache Kafka accepts."""
from __future__ import annotations

import base64
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from warehouse_common import kafka
from warehouse_common.settings import Settings


class FakeConsumer:
    seen: dict = {}

    def __init__(self, config: dict) -> None:
        FakeConsumer.seen = config

    def subscribe(self, topics: list[str]) -> None:
        pass

    def close(self) -> None:
        pass


class FakeCredentials:
    """Looks like the Workload Identity credentials a pod gets from the metadata server."""

    def __init__(self) -> None:
        self.token = "ya29.access-token"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) + timedelta(hours=1)
        self.service_account_email = "picking@demo.iam.gserviceaccount.com"
        self.valid = True


def segment(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()


def test_group_session_ends_before_a_new_leader_is_elected(monkeypatch) -> None:
    """A dead leader's partitions must be free by the time its successor asks for them."""
    monkeypatch.setattr(kafka, "Consumer", FakeConsumer)
    settings = Settings.from_env("test")
    stop = threading.Event()
    stop.set()

    kafka.consume(settings, "group", ["topic"], lambda topic, event: None, stop)

    assert FakeConsumer.seen["session.timeout.ms"] <= settings.zk_session_timeout * 1000
    assert FakeConsumer.seen["heartbeat.interval.ms"] * 3 <= FakeConsumer.seen["session.timeout.ms"]


def test_local_broker_uses_the_configured_protocol_and_no_token() -> None:
    config = kafka.client_config(Settings.from_env("test"))

    assert config["security.protocol"] == "PLAINTEXT"
    assert "oauth_cb" not in config


def test_managed_kafka_gets_sasl_over_tls_with_a_google_token() -> None:
    config = kafka.client_config(replace(Settings.from_env("test"), kafka_auth="google"))

    assert config["security.protocol"] == "SASL_SSL"
    assert config["sasl.mechanisms"] == "OAUTHBEARER"
    assert config["ssl.ca.location"] == "probe"
    assert isinstance(config["oauth_cb"], kafka.GoogleTokenProvider)


def test_token_wraps_the_access_token_and_names_the_service_account(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_MANAGED_KAFKA_AUTH_PRINCIPAL", raising=False)
    credentials = FakeCredentials()

    token, expiry = kafka.GoogleTokenProvider(credentials)("")

    header, claims, access = token.split(".")
    assert json.loads(segment(header)) == {"typ": "JWT", "alg": "GOOG_OAUTH2_TOKEN"}
    claims = json.loads(segment(claims))
    assert claims["sub"] == "picking@demo.iam.gserviceaccount.com"
    assert claims["iss"] == "Google"
    assert claims["exp"] == expiry
    assert segment(access) == "ya29.access-token"
    assert "=" not in token


def test_expired_credentials_are_refreshed_before_use(monkeypatch) -> None:
    credentials = FakeCredentials()
    credentials.valid = False
    refreshed = []
    credentials.refresh = lambda request: refreshed.append(request) or setattr(credentials, "valid", True)

    kafka.GoogleTokenProvider(credentials)("")

    assert len(refreshed) == 1
