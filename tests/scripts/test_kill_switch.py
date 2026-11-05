"""Drives the billing kill switch with fake budget notifications and a fake Google API.

A mistake here would either leave billing on after the credit runs out, or switch the
project off during a demo, so every branch is checked without touching Google Cloud.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("kill_switch", ROOT / "infra" / "bootstrap" / "killswitch" / "main.py")
kill_switch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kill_switch)


class Reply:
    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        pass


class FakeGoogle:
    """Records each call to the Cloud Billing and Resource Manager APIs."""

    def __init__(self, billing_enabled: bool = True, may_unlink: bool = True) -> None:
        self.billing_enabled = billing_enabled
        self.may_unlink = may_unlink
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, url):
        self.calls.append(("GET", url, None))
        return Reply({"billingEnabled": self.billing_enabled})

    def put(self, url, json=None):
        self.calls.append(("PUT", url, json))
        return Reply({})

    def post(self, url, json=None):
        self.calls.append(("POST", url, json))
        return Reply({"permissions": [kill_switch.NEEDED_PERMISSION] if self.may_unlink else []})

    @property
    def unlinked(self) -> bool:
        return ("PUT", kill_switch.BILLING_INFO.format("demo"), {"billingAccountName": ""}) in self.calls


def notification(cost: float, budget: float = 250, check: bool = False):
    data = base64.b64encode(json.dumps({"costAmount": cost, "budgetAmount": budget, "currencyCode": "USD"}).encode())
    message = {"data": data.decode(), "attributes": {"check": "true"} if check else {}}
    return SimpleNamespace(data={"message": message})


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "demo")
    fake = FakeGoogle()
    monkeypatch.setattr(kill_switch, "session", lambda: fake)
    return fake


def test_under_budget_touches_nothing(google):
    kill_switch.stop_billing(notification(cost=120))

    assert google.calls == []


def test_reaching_the_budget_unlinks_billing(google):
    kill_switch.stop_billing(notification(cost=250))

    assert google.unlinked


def test_billing_already_off_is_left_alone(google):
    google.billing_enabled = False

    kill_switch.stop_billing(notification(cost=300))

    assert not google.unlinked


def test_self_test_reports_permission_without_unlinking(google, capsys):
    kill_switch.stop_billing(notification(cost=10_000, check=True))

    assert not google.unlinked
    report = json.loads(capsys.readouterr().out.strip())
    assert report["message"] == "self-test" and report["can_unlink_billing"] is True


def test_self_test_flags_a_missing_permission(google, capsys):
    google.may_unlink = False

    kill_switch.stop_billing(notification(cost=0, check=True))

    report = json.loads(capsys.readouterr().out.strip())
    assert report["severity"] == "ERROR" and report["can_unlink_billing"] is False
