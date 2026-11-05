"""Generates the Grafana dashboards in gitops/platform/dashboards.

Hand-written dashboard JSON is long and easy to break, and Grafana's own exports are
worse to review. Each panel here is one line, so the dashboards stay readable in Git.

Run with `make dashboards` after changing this file.
"""
from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "gitops" / "platform" / "dashboards"
PANEL_WIDTH = 12
PANEL_HEIGHT = 8


def panel(index: int, title: str, targets: dict[str, str], unit: str = "short", kind: str = "timeseries") -> dict:
    return {
        "id": index + 1,
        "title": title,
        "type": kind,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "gridPos": {
            "h": PANEL_HEIGHT,
            "w": PANEL_WIDTH,
            "x": 0 if index % 2 == 0 else PANEL_WIDTH,
            "y": PANEL_HEIGHT * (index // 2),
        },
        "targets": [
            {"expr": expr, "legendFormat": legend, "refId": chr(ord("A") + i)}
            for i, (legend, expr) in enumerate(targets.items())
        ],
    }


def dashboard(uid: str, title: str, panels: list[dict]) -> dict:
    return {
        "uid": uid,
        "title": title,
        "tags": ["warehouse"],
        "schemaVersion": 39,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "panels": [panel(i, *args) if isinstance(args, tuple) else args for i, args in enumerate(panels)],
    }


SERVICE_RED = dashboard(
    "warehouse-service-red",
    "Service RED",
    [
        ("Requests per second", {"{{service}}": 'sum by (service) (rate(http_requests_total{namespace="warehouse"}[1m]))'}, "reqps"),
        # The `or` keeps the panel at 0% when nothing fails. With no 5xx series at all, the plain
        # ratio returns nothing, and Grafana would show a healthy system as "No data".
        ("HTTP error rate", {"{{service}}": '(sum by (service) (rate(http_requests_total{namespace="warehouse",status=~"5.."}[1m])) or 0 * sum by (service) (rate(http_requests_total{namespace="warehouse"}[1m]))) / sum by (service) (rate(http_requests_total{namespace="warehouse"}[1m]))'}, "percentunit"),
        ("HTTP latency p95", {"{{service}}": 'histogram_quantile(0.95, sum by (service, le) (rate(http_request_duration_seconds_bucket{namespace="warehouse"}[1m])))'}, "s"),
        ("gRPC requests per second", {"{{service}} {{method}}": 'sum by (service, method) (rate(grpc_server_requests_total{namespace="warehouse"}[1m]))'}, "reqps"),
        ("gRPC latency p95", {"{{service}}": 'histogram_quantile(0.95, sum by (service, le) (rate(grpc_server_request_duration_seconds_bucket{namespace="warehouse"}[1m])))'}, "s"),
        ("Kafka events per second", {"produced {{topic}}": "sum by (topic) (rate(kafka_events_produced_total[1m]))", "consumed {{topic}}": "sum by (topic) (rate(kafka_events_consumed_total[1m]))"}, "ops"),
    ],
)

CLUSTER_HEALTH = dashboard(
    "warehouse-cluster-health",
    "Cluster Health",
    [
        ("Pod replicas per Deployment", {"{{deployment}}": 'kube_deployment_status_replicas{namespace="warehouse"}'}),
        ("HPA current vs desired", {"{{horizontalpodautoscaler}} current": 'kube_horizontalpodautoscaler_status_current_replicas{namespace="warehouse"}', "{{horizontalpodautoscaler}} desired": 'kube_horizontalpodautoscaler_status_desired_replicas{namespace="warehouse"}'}),
        ("Pod CPU", {"{{pod}}": 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="warehouse",container!=""}[1m]))'}, "cores"),
        ("Pod memory", {"{{pod}}": 'sum by (pod) (container_memory_working_set_bytes{namespace="warehouse",container!=""})'}, "bytes"),
        ("Node CPU in use", {"{{instance}}": '1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m]))'}, "percentunit"),
        ("Node memory in use", {"{{instance}}": "1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes"}, "percentunit"),
        ("Pod restarts in the last 15 minutes", {"{{pod}}": 'sum by (pod) (increase(kube_pod_container_status_restarts_total{namespace="warehouse"}[15m]))'}),
        ("Pods not ready", {"not ready": 'sum(kube_pod_status_ready{namespace="warehouse",condition="false"})'}),
    ],
)

ZOOKEEPER = dashboard(
    "warehouse-zookeeper",
    "ZooKeeper",
    [
        ("Ensemble nodes up", {"nodes up": 'sum(up{service="zookeeper-metrics"})'}),
        ("Live client sessions", {"{{pod}}": 'num_alive_connections{namespace="warehouse"}'}),
        ("Znodes", {"{{pod}}": 'znode_count{namespace="warehouse"}'}),
        ("Ephemeral znodes", {"{{pod}}": 'ephemerals_count{namespace="warehouse"}'}),
        ("Request latency", {"{{pod}}": 'avg_latency{namespace="warehouse"}'}, "ms"),
        ("Outstanding requests", {"{{pod}}": 'outstanding_requests{namespace="warehouse"}'}),
        ("Scheduler leader", {"{{pod}}": "warehouse_scheduler_is_leader == 1"}),
        ("Leader epoch", {"epoch": "max(warehouse_scheduler_epoch)"}),
    ],
)

WAREHOUSE_KPIS = dashboard(
    "warehouse-kpis",
    "Warehouse KPIs",
    [
        ("Tasks waiting for a worker", {"{{task_type}}": "max by (task_type) (warehouse_queue_depth)"}),
        ("Live workers", {"{{capability}}": "max by (capability) (warehouse_live_workers)"}),
        ("Tasks assigned per second", {"{{task_type}}": "sum by (task_type) (rate(warehouse_tasks_assigned_total[1m]))"}, "ops"),
        ("Tasks finished per second", {"{{task_type}} {{outcome}}": "sum by (task_type, outcome) (rate(warehouse_worker_tasks_total[1m]))"}, "ops"),
        ("Abandoned tasks reclaimed", {"{{reason}}": "sum by (reason) (increase(warehouse_tasks_abandoned_total[5m]))"}),
        ("Task recovery time p95", {"p95": "histogram_quantile(0.95, sum by (le) (rate(warehouse_task_recovery_seconds_bucket[5m])))"}, "s"),
        ("Late completions rejected by fencing", {"fenced": "sum(increase(warehouse_fenced_completions_total[5m]))"}),
        ("Flink window: throughput per zone", {"{{zone}} {{metric}}": 'max by (zone, metric) (warehouse_window_value{metric=~"picks_completed|packs_completed|dispatched"})'}),
        ("Flink window: queue depth per zone", {"{{zone}}": 'max by (zone) (warehouse_window_value{metric="queue_depth"})'}),
        ("Flink window: active workers", {"{{zone}}": 'max by (zone) (warehouse_window_value{metric="active_workers"})'}),
    ],
)

# Every source writes to Cloud Logging: GKE containers through GKE's own agent, the
# Dispatch function through Cloud Run, and Flink through Dataproc's YARN container logs.
WAREHOUSE_LOGS = (
    '(resource.type="k8s_container" AND resource.labels.namespace_name="warehouse")'
    ' OR (resource.type="cloud_run_revision" AND resource.labels.service_name="warehouse-dispatch")'
    ' OR (resource.type="cloud_dataproc_cluster" AND resource.labels.cluster_name="warehouse-analytics")'
)


def logs_panel(index: int, title: str, query: str) -> dict:
    return {
        "id": index + 1,
        "title": title,
        "type": "logs",
        "datasource": {"type": "googlecloud-logging-datasource", "uid": "cloud-logging"},
        "gridPos": {"h": 12, "w": 24, "x": 0, "y": 12 * index},
        "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"},
        # Grafana fills in the cluster's own project only for a query in "query", so the
        # dashboard holds no project id. "queryText" is the older field, kept for older
        # plugin versions.
        "targets": [{"refId": "A", "query": query, "queryText": query}],
    }


CENTRAL_LOGS = dashboard(
    "warehouse-logs",
    "Central Logs",
    [
        logs_panel(0, "One order across every service, Flink and Dispatch included", f'({WAREHOUSE_LOGS}) AND jsonPayload.order_id="$order_id"'),
        logs_panel(1, "Warnings and errors", f"({WAREHOUSE_LOGS}) AND severity>=WARNING"),
        logs_panel(2, "All warehouse logs", WAREHOUSE_LOGS),
    ],
)
CENTRAL_LOGS["templating"] = {
    "list": [{"name": "order_id", "label": "Order id", "type": "textbox", "query": "", "current": {"value": ""}}]
}

DASHBOARDS = {
    "service-red.json": SERVICE_RED,
    "cluster-health.json": CLUSTER_HEALTH,
    "zookeeper.json": ZOOKEEPER,
    "warehouse-kpis.json": WAREHOUSE_KPIS,
    "central-logs.json": CENTRAL_LOGS,
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, content in DASHBOARDS.items():
        (OUTPUT / name).write_text(json.dumps(content, indent=2) + "\n")


if __name__ == "__main__":
    main()
