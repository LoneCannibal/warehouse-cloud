"""The Warehouse Console page: Order Intake serves it, and it calls only public routes."""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app import main

ROOT = Path(__file__).resolve().parents[3]
PAGE = main.CONSOLE.read_text()


def public_paths() -> list[str]:
    ingress = yaml.safe_load((ROOT / "gitops" / "services" / "base" / "ingress.yaml").read_text())
    return [p["path"] for rule in ingress["spec"]["rules"] for p in rule["http"]["paths"]]


def exposed(path: str) -> bool:
    """GKE's rules: a path ending in /* matches by prefix, any other path exactly."""
    path = path.split("?")[0]
    for rule in public_paths():
        if rule.endswith("/*") and path.startswith(rule[:-1]):
            return True
        if path == rule:
            return True
    return False


def test_the_root_serves_the_console():
    # No `with`, so the lifespan (database, Kafka, gRPC) does not start.
    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Smart Warehouse</title>" in response.text


def test_the_load_balancer_sends_the_root_to_order_intake():
    assert "/" in public_paths()


def test_the_page_calls_only_routes_the_load_balancer_exposes():
    routes = dict(re.findall(r'(\w+): "(/v1/[^"]*)"', PAGE.split("const API = {", 1)[1].split("};", 1)[0]))

    assert set(routes) == {"orders", "floor", "workers", "analytics", "windows", "stock"}
    for path in routes.values():
        assert exposed(path), path
    # Reading one order and listing stock add a suffix or a query to the base route.
    assert exposed(routes["orders"] + "/ord-1234")
    assert exposed(routes["stock"] + "?zone=A&limit=100")
    assert exposed(routes["orders"] + "?ids=ord-1,ord-2")
    assert exposed(routes["windows"] + "?zone=A&limit=30")


def test_private_routes_stay_private():
    for path in ("/health", "/metrics", "/docs", "/chaos/hang"):
        assert not exposed(path), path


def test_the_page_needs_nothing_from_another_site():
    assert not re.search(r'(src|href)="https?://', PAGE)
    script = PAGE.split("<script>", 1)[1].replace('"http://www.w3.org/2000/svg"', "")  # a name, not a request
    assert "fetch(path" in PAGE
    assert not re.search(r"https?://", script)
