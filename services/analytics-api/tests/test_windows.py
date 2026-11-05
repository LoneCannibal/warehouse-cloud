"""Stores and reads Flink windows through the in-memory Firestore stand-in."""
from __future__ import annotations

import pytest

from app import main
from warehouse_common.fakes import Firestore


def window(zone: str, start: int, picks: int = 0) -> dict:
    result = {metric: 0 for metric in main.WINDOW_METRICS}
    result.update(zone=zone, window_start=start, window_end=start + 60_000, picks_completed=picks)
    return result


@pytest.fixture
def zones(monkeypatch):
    store = Firestore().collection("analytics_zones")
    monkeypatch.setitem(main.state, "zones", store)
    return store


def test_a_redelivered_window_overwrites_itself(zones):
    main.on_result("results", window("A", 60_000, picks=3))
    main.on_result("results", window("A", 60_000, picks=3))

    assert len(main.windows("A")["windows"]) == 1


def test_windows_come_back_newest_first_and_limited(zones):
    for start in (0, 120_000, 60_000):
        main.on_result("results", window("A", start))

    starts = [w["window_start"] for w in main.windows("A", limit=2)["windows"]]

    assert starts == [120_000, 60_000]


def test_latest_returns_one_window_per_zone_that_has_any(zones):
    main.on_result("results", window("A", 0))
    main.on_result("results", window("A", 60_000, picks=7))
    main.on_result("results", window("P", 0))

    latest = {w["zone"]: w for w in main.latest()["windows"]}

    assert set(latest) == {"A", "P"}
    assert latest["A"]["picks_completed"] == 7


def test_results_also_become_prometheus_gauges(zones):
    main.on_result("results", window("B", 0, picks=5))

    assert main.WINDOW_GAUGE.labels("B", "picks_completed")._value.get() == 5
