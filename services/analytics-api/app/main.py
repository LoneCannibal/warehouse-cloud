"""Warehouse Analytics read side.

The Flink job on Dataproc computes the windows and cannot answer queries, so this
service reads the results topic, stores each window in Firestore, and serves them
over REST and as Prometheus gauges for the Warehouse KPIs dashboard.

Windows live at analytics_zones/{zone}/windows/{window_start}. Ordering one zone's
windows by a single field needs no composite index, so Terraform has none to create.
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from google.cloud import firestore
from prometheus_client import Gauge

from warehouse_common import gcp
from warehouse_common.events import TOPIC_RESULTS
from warehouse_common.kafka import consume
from warehouse_common.settings import Settings
from warehouse_common.web import create_app

log = logging.getLogger(__name__)
settings = Settings.from_env("analytics-api")
state: dict = {}

ZONES = ("A", "B", "C", "P", "D")
WINDOW_METRICS = (
    "tasks_created",
    "picks_completed",
    "packs_completed",
    "dispatched",
    "tasks_abandoned",
    "tasks_reclaimed",
    "active_workers",
    "queue_depth",
    "mean_recovery_ms",
)
WINDOW_GAUGE = Gauge("warehouse_window_value", "Latest Flink window result.", ["zone", "metric"])
WINDOW_END = Gauge("warehouse_window_end_timestamp_seconds", "End of the latest Flink window.", ["zone"])


def window_collection(zone: str):
    return state["zones"].document(zone).collection("windows")


def on_result(_topic: str, result: dict) -> None:
    # The document id is the window start, so a redelivered window overwrites itself.
    window_collection(result["zone"]).document(str(result["window_start"])).set(result)
    zone = result["zone"]
    for metric in WINDOW_METRICS:
        WINDOW_GAUGE.labels(zone, metric).set(result[metric])
    WINDOW_END.labels(zone).set(result["window_end"] / 1000)
    log.info("window stored: zone %s ending %s", zone, result["window_end"])


@asynccontextmanager
async def lifespan(_app):
    state["zones"] = gcp.firestore_client(settings).collection(settings.analytics_collection)
    stop = threading.Event()
    consumer = threading.Thread(
        target=consume,
        args=(settings, "analytics-api", [TOPIC_RESULTS], on_result, stop),
        name="results-consumer",
        daemon=True,
    )
    consumer.start()
    log.info("analytics api ready")
    yield
    stop.set()
    consumer.join(timeout=5)


app = create_app(settings.service, lifespan)


def _windows(zone: str, limit: int) -> list[dict]:
    newest_first = window_collection(zone).order_by("window_start", direction=firestore.Query.DESCENDING)
    return [snapshot.to_dict() for snapshot in newest_first.limit(limit).stream()]


@app.get("/v1/analytics/latest")
def latest() -> dict:
    return {"windows": [window for zone in ZONES for window in _windows(zone, 1)]}


@app.get("/v1/analytics/windows")
def windows(zone: str, limit: int = 30) -> dict:
    return {"zone": zone, "windows": _windows(zone, min(limit, 200))}
