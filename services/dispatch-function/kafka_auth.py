"""The OAUTHBEARER token that Managed Service for Apache Kafka expects.

A copy of GoogleTokenProvider in libs/warehouse_common/warehouse_common/kafka.py,
because the function is deployed on its own, without the shared library. A unit
test compares the two, so they cannot drift apart unnoticed.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from datetime import timezone


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class GoogleTokenProvider:
    HEADER = json.dumps({"typ": "JWT", "alg": "GOOG_OAUTH2_TOKEN"})

    def __init__(self, credentials=None) -> None:
        self._credentials = credentials
        self._lock = threading.Lock()

    def __call__(self, _config: str) -> tuple[str, float]:
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
