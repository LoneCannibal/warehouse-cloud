"""Unlinks the project from its billing account once spending reaches the budget.

The budget in budget.tf publishes its running total to Pub/Sub several times a day.
When the cost reaches the budget amount, this function removes the project's billing
account. Google then stops every resource in the project, so nothing more can be
charged. Data in stopped resources can be lost, which is why this is the last layer
of protection, not the first.

A message with the attribute check=true runs a harmless self-test instead: it reports
whether this function holds the permission it needs. `make killswitch-check` sends one.
"""
from __future__ import annotations

import base64
import json
import os

import functions_framework

BILLING_INFO = "https://cloudbilling.googleapis.com/v1/projects/{}/billingInfo"
TEST_PERMISSIONS = "https://cloudresourcemanager.googleapis.com/v1/projects/{}:testIamPermissions"
NEEDED_PERMISSION = "resourcemanager.projects.deleteBillingAssignment"


def log(severity: str, message: str, **fields) -> None:
    print(json.dumps({"severity": severity, "message": message, **fields}), flush=True)


def session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials)


def over_budget(notification: dict) -> bool:
    return float(notification["costAmount"]) >= float(notification["budgetAmount"])


def can_unlink_billing(http, project: str) -> bool:
    reply = http.post(TEST_PERMISSIONS.format(project), json={"permissions": [NEEDED_PERMISSION]})
    reply.raise_for_status()
    return NEEDED_PERMISSION in reply.json().get("permissions", [])


def unlink_billing(http, project: str) -> None:
    info = http.get(BILLING_INFO.format(project))
    info.raise_for_status()
    if not info.json().get("billingEnabled"):
        log("NOTICE", "billing is already off", project=project)
        return
    reply = http.put(BILLING_INFO.format(project), json={"billingAccountName": ""})
    reply.raise_for_status()
    log("CRITICAL", "billing unlinked: every resource in the project is stopping", project=project)


@functions_framework.cloud_event
def stop_billing(cloud_event) -> None:
    project = os.environ["PROJECT_ID"]
    message = cloud_event.data["message"]
    attributes = message.get("attributes") or {}

    if attributes.get("check") == "true":
        allowed = can_unlink_billing(session(), project)
        log("NOTICE" if allowed else "ERROR", "self-test", project=project, can_unlink_billing=allowed)
        return

    notification = json.loads(base64.b64decode(message["data"]))
    cost, budget = notification["costAmount"], notification["budgetAmount"]
    if not over_budget(notification):
        log("INFO", "under budget", cost=cost, budget=budget, currency=notification.get("currencyCode"))
        return

    log("WARNING", "budget reached", cost=cost, budget=budget, currency=notification.get("currencyCode"))
    unlink_billing(session(), project)
