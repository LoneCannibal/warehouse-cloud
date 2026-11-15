"""Checks the rules that schema validation cannot see.

A manifest can be perfectly valid Kubernetes and still break the system. Each test here
guards one of those mistakes. The gitops/ tree is read as plain YAML, so this needs no
cluster and no Google Cloud project.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "gitops" / "services" / "base"
SERVICES = sorted(p.name for p in BASE.iterdir() if p.is_dir())


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def deployment(service: str) -> dict:
    return documents(BASE / service / "deployment.yaml")[0]


def container(service: str) -> dict:
    return deployment(service)["spec"]["template"]["spec"]["containers"][0]


def services_with_hpa() -> list[str]:
    return [s for s in SERVICES if (BASE / s / "hpa.yaml").exists()]


def test_the_seven_kubernetes_services_are_all_present():
    assert SERVICES == [
        "analytics-api",
        "inventory",
        "job-scheduler",
        "order-intake",
        "packing",
        "picking",
        "worker-registry",
    ]


def test_at_least_two_critical_services_autoscale_on_cpu():
    assert services_with_hpa() == ["order-intake", "picking"]
    for service in services_with_hpa():
        metric = documents(BASE / service / "hpa.yaml")[0]["spec"]["metrics"][0]
        assert metric["resource"]["name"] == "cpu"


@pytest.mark.parametrize("service", services_with_hpa())
def test_autoscaled_deployments_leave_replicas_to_the_hpa(service):
    # A replicas field would make Argo CD self-heal undo every scale-out.
    assert "replicas" not in deployment(service)["spec"]


@pytest.mark.parametrize("service", SERVICES)
def test_cpu_requests_exist(service):
    # An HPA computes utilization as a share of the request, so no request means no scaling.
    assert "cpu" in container(service)["resources"]["requests"]


def test_the_scheduler_has_standbys_and_no_autoscaler():
    assert deployment("job-scheduler")["spec"]["replicas"] == 3
    assert "job-scheduler" not in services_with_hpa()


@pytest.mark.parametrize("service", ["inventory", "job-scheduler", "worker-registry"])
def test_grpc_services_are_headless(service):
    grpc = next(d for d in documents(BASE / service / "service.yaml") if d["metadata"]["name"].endswith("-grpc"))
    assert grpc["spec"]["clusterIP"] == "None"


def test_grpc_targets_use_dns_resolution_so_round_robin_sees_every_pod():
    config = documents(BASE / "config.yaml")[0]["data"]
    for key in ("INVENTORY_TARGET", "WORKERS_TARGET", "SCHEDULER_TARGET"):
        assert config[key].startswith("dns:///")


@pytest.mark.parametrize("service", ["picking", "packing", "job-scheduler"])
def test_identity_in_zookeeper_comes_from_the_pod_name(service):
    env = {item["name"]: item for item in container(service)["env"]}
    assert env["WORKER_ID"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.name"


@pytest.mark.parametrize(
    "service", ["order-intake", "job-scheduler", "worker-registry", "picking", "packing", "analytics-api"]
)
def test_services_that_call_google_apis_use_their_own_workload_identity(service):
    # Terraform annotates a Kubernetes service account of this name with its Google
    # service account. Without it the pod could not sign in to Kafka or Firestore.
    assert deployment(service)["spec"]["template"]["spec"]["serviceAccountName"] == service


def test_the_public_load_balancer_is_googles():
    annotations = documents(BASE / "ingress.yaml")[0]["metadata"]["annotations"]
    assert annotations["kubernetes.io/ingress.class"] == "gce"


def test_chaos_routes_are_not_public():
    rules = documents(BASE / "ingress.yaml")[0]["spec"]["rules"][0]["http"]["paths"]
    paths = [rule["path"] for rule in rules]
    assert "/v1/orders" in paths
    assert not any("chaos" in path for path in paths)
    # On GKE a path without "*" matches exactly, so "/" serves only the console page.
    # A catch-all "/*" would also expose /chaos, /health and /metrics.
    assert "/*" not in paths
    root = [rule for rule in rules if rule["path"] == "/"]
    assert all(rule["backend"]["service"]["name"] == "order-intake" for rule in root)


def test_no_secret_value_is_committed():
    for service in SERVICES:
        for item in container(service).get("env", []):
            assert "DB_DSN" != item["name"] or "secretKeyRef" in item["valueFrom"]


def zookeeper_statefulset() -> dict:
    docs = documents(ROOT / "gitops" / "platform" / "zookeeper" / "zookeeper.yaml")
    return next(d for d in docs if d["kind"] == "StatefulSet")


def test_zookeeper_has_an_odd_quorum_spread_across_nodes():
    spec = zookeeper_statefulset()["spec"]
    assert spec["replicas"] == 3
    assert spec["template"]["spec"]["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]


def test_zookeeper_starts_in_parallel_or_quorum_never_forms():
    assert zookeeper_statefulset()["spec"]["podManagementPolicy"] == "Parallel"


def test_zookeeper_volumes_go_with_the_app_so_teardown_leaves_nothing_billing():
    policy = zookeeper_statefulset()["spec"]["persistentVolumeClaimRetentionPolicy"]
    assert policy["whenDeleted"] == "Delete"


def test_every_application_has_the_finalizer_that_makes_teardown_work():
    templates = ROOT / "gitops" / "apps" / "templates"
    text = "".join(p.read_text() for p in (templates / "platform.yaml", templates / "services.yaml"))
    assert text.count("kind: Application") == text.count('include "warehouse.metadata"')
    assert "resources-finalizer.argocd.argoproj.io" in (templates / "_application.tpl").read_text()


def test_every_application_self_heals():
    templates = ROOT / "gitops" / "apps" / "templates"
    assert "selfHeal: true" in (templates / "_application.tpl").read_text()
