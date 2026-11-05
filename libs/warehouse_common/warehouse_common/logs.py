"""JSON logging to stdout.

Every service logs one JSON object per line, with order_id, task_id and
worker_id as top-level fields, so one Cloud Logging query can follow a single
order across all the services. Cloud Logging reads `severity` and `message` from
each line, so errors show as errors there.
"""
from __future__ import annotations

import json
import logging
import sys
import time

CONTEXT_FIELDS = ("order_id", "task_id", "worker_id", "attempt", "epoch", "event_type")


class JsonFormatter(logging.Formatter):
    converter = time.gmtime

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%S')}.{int(record.msecs):03d}Z",
            "level": record.levelname,
            "severity": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(service: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("kazoo", "httpx", "httpcore", "uvicorn.access", "google", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
