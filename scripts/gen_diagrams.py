"""Draws the design document's diagrams into docs/diagrams.

They live as code so that a design change means a reviewable diff, not a redrawn
image. Needs matplotlib, which `make install` adds. Run with `make diagrams`.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "diagrams"

INK = "#1f2933"
SERVICE = "#dbeafe"
STORE = "#dcfce7"
MANAGED = "#fef3c7"
SERVERLESS = "#fce7f3"
ZOOKEEPER = "#ede9fe"
GROUP = "#f8fafc"

REST = "#2563eb"
GRPC = "#dc2626"
KAFKA = "#059669"
ZK_LINE = "#7c3aed"
EVENT = "#d97706"
DATA = "#64748b"


def canvas(width: float, height: float):
    figure, axes = plt.subplots(figsize=(width, height))
    axes.set_xlim(0, width)
    axes.set_ylim(0, height)
    axes.axis("off")
    return figure, axes


def box(axes, x, y, width, height, text, fill=SERVICE, size=9, bold=True):
    axes.add_patch(
        FancyBboxPatch(
            (x, y), width, height, boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=1.1, edgecolor=INK, facecolor=fill, zorder=2,
        )
    )
    axes.text(
        x + width / 2, y + height / 2, text, ha="center", va="center", fontsize=size,
        color=INK, weight="bold" if bold else "normal", zorder=3,
    )


def group(axes, x, y, width, height, title, fill=GROUP, title_right=False):
    axes.add_patch(
        FancyBboxPatch(
            (x, y), width, height, boxstyle="round,pad=0.02,rounding_size=0.1",
            linewidth=1.1, edgecolor="#94a3b8", facecolor=fill, linestyle="--", zorder=1,
        )
    )
    title_x = x + width - 0.12 if title_right else x + 0.12
    axes.text(
        title_x, y + height - 0.1, title, ha="right" if title_right else "left", va="top",
        fontsize=9, color="#475569", weight="bold", zorder=3,
    )


def link(axes, start, end, color=INK, label=None, offset=(0, 0.1), style="-|>", dashed=False, curve=0.0):
    axes.add_patch(
        FancyArrowPatch(
            start, end, arrowstyle=style, mutation_scale=12, linewidth=1.5, color=color,
            linestyle="--" if dashed else "-", connectionstyle=f"arc3,rad={curve}", zorder=4,
        )
    )
    if label:
        axes.text(
            (start[0] + end[0]) / 2 + offset[0], (start[1] + end[1]) / 2 + offset[1], label,
            ha="center", va="center", fontsize=7.5, color=color, zorder=5,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.8, "alpha": 0.9},
        )


def lane(axes, points, color, label=None, label_at=None, dashed=False):
    """An arrow with right-angle bends, for routing around boxes."""
    xs = [point[0] for point in points[:-1]]
    ys = [point[1] for point in points[:-1]]
    axes.plot(xs, ys, color=color, linewidth=1.5, linestyle="--" if dashed else "-", zorder=4)
    link(axes, points[-2], points[-1], color, dashed=dashed)
    if label:
        axes.text(
            *label_at, label, ha="center", va="center", fontsize=7.5, color=color, zorder=5,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.8, "alpha": 0.95},
        )


def legend(axes, x, y, entries, step=0.26):
    for index, (color, text) in enumerate(entries):
        row = y - index * step
        axes.plot([x, x + 0.4], [row, row], color=color, linewidth=2)
        axes.text(x + 0.5, row, text, va="center", fontsize=8, color=INK)


def save(figure, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT / name, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def deployment() -> None:
    figure, axes = canvas(13, 8.6)

    box(axes, 0.3, 6.85, 1.7, 0.7, "Clients / k6", "#e2e8f0")
    group(axes, 0.1, 3.55, 2.1, 2.95, "GitHub")
    box(axes, 0.3, 5.05, 1.7, 0.85, "Repository\ncode, Terraform,\nmanifests", "#e2e8f0", 7.5)
    box(axes, 0.3, 3.75, 1.7, 0.8, "GitHub Actions\nCI, Workload\nIdentity Federation", "#e2e8f0", 7)
    box(axes, 0.3, 2.35, 1.7, 0.7, "Terraform", "#e2e8f0")
    link(axes, (1.15, 5.05), (1.15, 4.55), INK)

    group(axes, 2.5, 0.2, 10.4, 8.2, "Google Cloud project, region asia-south1, paid for by the group's credit")
    group(axes, 2.7, 1.75, 8.0, 6.35, "VPC network, nodes without public addresses", "#ffffff")
    group(axes, 2.9, 6.8, 2.2, 0.95, "Public edge", "#f1f5f9")
    box(axes, 3.05, 6.87, 0.95, 0.5, "Load\nbalancer", MANAGED, 7.5)
    box(axes, 4.07, 6.87, 0.95, 0.5, "Cloud\nNAT", MANAGED, 7.5)

    group(axes, 2.9, 1.95, 7.6, 4.7, "Private subnets: gke, data, serverless", "#f1f5f9", title_right=True)
    group(axes, 3.05, 3.45, 4.5, 2.9, "GKE zonal, 3 n2-standard-2 nodes", "#eff6ff", title_right=True)
    box(axes, 3.2, 5.3, 2.05, 0.65, "7 service Deployments\nHPA: Order Intake, Picking", SERVICE, 7)
    box(axes, 5.35, 5.3, 2.05, 0.65, "ZooKeeper ensemble\n3 pods, persistent disks", ZOOKEEPER, 7.5)
    box(axes, 3.2, 4.45, 2.05, 0.65, "Argo CD", MANAGED, 8.5)
    box(axes, 5.35, 4.45, 2.05, 0.65, "Prometheus, Grafana", MANAGED, 8.5)
    box(axes, 3.2, 3.6, 4.2, 0.65, "Built into GKE: Ingress controller, log agent,\nmetrics-server", MANAGED, 7.5)

    box(axes, 7.95, 5.3, 2.4, 0.65, "Managed Kafka\n3 vCPU, SASL over TLS", MANAGED, 8)
    box(axes, 7.95, 4.4, 2.4, 0.65, "Dataproc 2.2, Flink 1.17\none node", MANAGED, 8)
    box(axes, 7.95, 3.5, 2.4, 0.65, "Cloud SQL PostgreSQL 16\nprivate IP, schema per service", STORE, 7.5)
    box(axes, 3.2, 2.2, 3.2, 0.75, "Dispatch: Cloud Run function\nDirect VPC egress to Kafka", SERVERLESS, 8)
    box(axes, 7.95, 2.2, 2.4, 0.75, "Private Google Access\nto Google APIs", "#ffffff", 8)

    group(axes, 2.7, 0.35, 8.0, 1.25, "Google-managed services", "#ffffff")
    box(axes, 2.9, 0.55, 1.4, 0.7, "Cloud Storage\ndata bucket", STORE, 7.5)
    box(axes, 4.45, 0.55, 1.4, 0.7, "Pub/Sub, with\ndead letters", MANAGED, 7.5)
    box(axes, 6.0, 0.55, 1.5, 0.7, "Firestore\n3 collections", STORE, 8)
    box(axes, 7.65, 0.55, 1.45, 0.7, "Cloud\nLogging", MANAGED, 8)
    box(axes, 9.25, 0.55, 1.3, 0.7, "Artifact\nRegistry", MANAGED, 8)

    group(axes, 10.95, 1.75, 1.8, 6.35, "Access control", "#ffffff")
    box(axes, 11.08, 6.3, 1.54, 0.85, "IAM: one service\naccount per\nworkload", "#ffffff", 7.5)
    box(axes, 11.08, 5.1, 1.54, 0.85, "Workload\nIdentity,\nno key files", "#ffffff", 7.5)
    box(axes, 11.08, 3.9, 1.54, 0.85, "Budget alerts\nand billing\nkill switch", "#ffffff", 7.5)

    link(axes, (2.0, 7.12), (3.05, 7.12), REST, "public URL", (0, 0.16))
    link(axes, (3.5, 6.87), (3.5, 5.95), REST)
    link(axes, (3.2, 4.75), (2.0, 5.45), GRPC, "Argo CD\npulls Git", (0.02, 0.32), dashed=True)
    link(axes, (2.0, 2.7), (2.5, 2.7), INK, "creates\nall of it", (0.0, 0.32))
    link(axes, (7.4, 5.62), (7.95, 5.62), KAFKA, style="<|-|>")
    link(axes, (9.15, 5.05), (9.15, 5.3), KAFKA, style="<|-|>")
    link(axes, (7.55, 3.82), (7.95, 3.82), DATA, style="<|-|>")
    link(axes, (5.15, 1.25), (5.15, 2.2), EVENT, "triggers", (0.42, 0))
    lane(axes, [(6.4, 2.6), (7.75, 2.6), (7.75, 5.45), (7.95, 5.45)], KAFKA)

    legend(axes, 0.2, 1.7, [
        (REST, "Client traffic"), (KAFKA, "Kafka"), (EVENT, "Pub/Sub push"),
        (DATA, "SQL"), (GRPC, "GitOps pull"), (INK, "Provisioning"),
    ])
    save(figure, "deployment-architecture.png")


def microservices() -> None:
    figure, axes = canvas(13, 7.8)

    box(axes, 0.2, 5.6, 1.5, 0.8, "Client / k6", "#e2e8f0")
    box(axes, 2.3, 5.6, 2.1, 0.8, "Order Intake\npublic, HPA")
    box(axes, 2.3, 3.75, 2.1, 0.8, "Inventory")
    box(axes, 6.1, 5.6, 2.3, 0.8, "Job Scheduler\n1 leader, 2 standbys")
    box(axes, 10.2, 5.6, 2.3, 0.8, "Worker Registry")
    box(axes, 6.1, 3.75, 2.3, 0.8, "ZooKeeper ensemble\n3 nodes", ZOOKEEPER)
    box(axes, 2.3, 1.95, 2.1, 0.8, "Picking\nworkers, HPA")
    box(axes, 6.1, 1.95, 2.3, 0.8, "Packing\nworkers")
    box(axes, 10.2, 1.95, 2.3, 0.8, "Dispatch\nCloud Run function", SERVERLESS, 8)
    box(axes, 10.2, 3.75, 2.3, 0.8, "Cloud Storage,\nthen Pub/Sub", STORE)
    box(axes, 1.2, 0.15, 7.2, 0.55, "Kafka: warehouse.task-events", "#a7f3d0")
    box(axes, 8.7, 0.15, 1.75, 0.55, "Flink on Dataproc\n1-minute windows", MANAGED, 7)
    box(axes, 10.75, 0.15, 2.0, 0.55, "Analytics API\n+ Firestore", SERVICE, 7.5)
    box(axes, 4.45, 6.85, 1.6, 0.6, "Kafka:\nwarehouse.orders", "#a7f3d0", 7.5)

    link(axes, (1.7, 6.0), (2.3, 6.0), REST, "REST", (0, 0.15))
    link(axes, (3.0, 5.6), (3.0, 4.55), GRPC, "ReserveStock", (-0.68, 0))
    link(axes, (3.8, 6.4), (4.6, 6.85), KAFKA)
    link(axes, (5.9, 6.85), (6.7, 6.4), KAFKA)
    link(axes, (4.4, 6.2), (6.1, 6.2), REST, "REST: order status", (0, -0.15))
    link(axes, (8.4, 6.0), (10.2, 6.0), GRPC, "ListAvailable", (0, 0.15))
    link(axes, (6.75, 5.6), (6.75, 4.55), ZK_LINE, "election,\noffers", (-0.5, 0), style="<|-|>")
    link(axes, (7.25, 3.75), (7.25, 2.75), ZK_LINE, "lease,\nmembership", (0.56, 0), style="<|-|>")
    link(axes, (4.4, 2.45), (6.1, 3.9), ZK_LINE, "lease,\nmembership", (0.61, 0.02), style="<|-|>")
    link(axes, (8.4, 4.3), (10.2, 5.65), ZK_LINE, "watches workers", (-0.28, 0.24))
    link(axes, (3.0, 2.75), (3.0, 3.75), GRPC, "ConfirmPick", (-0.62, 0))
    link(axes, (8.4, 2.45), (10.45, 3.75), EVENT, "manifest upload", (0.1, -0.22))
    link(axes, (11.35, 3.75), (11.35, 2.75), EVENT, "push", (0.32, 0))

    lane(axes, [(4.4, 2.6), (4.95, 2.6), (4.95, 5.72), (6.1, 5.72)], GRPC, "GetTask,\nCompleteTask", (4.5, 4.9))
    lane(axes, [(8.4, 2.6), (8.85, 2.6), (8.85, 5.72), (8.4, 5.72)], GRPC, "GetTask,\nCompleteTask", (9.35, 3.4))
    lane(axes, [(6.1, 5.9), (5.45, 5.9), (5.45, 0.7)], KAFKA, "lifecycle events\nvia the outbox", (5.45, 1.45))
    link(axes, (3.0, 1.95), (3.0, 0.7), KAFKA, "TASK_STARTED", (-0.72, 0))
    link(axes, (6.9, 1.95), (6.9, 0.7), KAFKA, "TASK_STARTED", (0.72, 0))
    link(axes, (10.8, 1.95), (8.2, 0.7), KAFKA, "SHIPMENT_DISPATCHED", (0.85, 0.06))
    link(axes, (8.4, 0.42), (8.7, 0.42), KAFKA)
    link(axes, (10.45, 0.42), (10.75, 0.42), KAFKA, "results", (0.02, 0.42))

    legend(axes, 0.2, 3.4, [
        (REST, "REST"), (GRPC, "gRPC"), (KAFKA, "Kafka"), (ZK_LINE, "ZooKeeper"), (EVENT, "Pub/Sub"),
    ])
    save(figure, "microservices.png")


def task_lifecycle() -> None:
    figure, axes = canvas(12, 5.2)

    box(axes, 0.3, 2.3, 1.7, 0.9, "PENDING\nattempt n", MANAGED)
    box(axes, 4.1, 2.3, 2.0, 0.9, "ASSIGNED\noffer in ZooKeeper", SERVICE)
    box(axes, 8.2, 2.3, 1.9, 0.9, "Worker holds\nephemeral lease", ZOOKEEPER)
    box(axes, 10.3, 0.3, 1.5, 0.9, "DONE", STORE)
    box(axes, 4.1, 0.3, 2.0, 0.9, "Late result\nREJECTED", "#fee2e2")
    box(axes, 0.3, 4.1, 3.0, 0.9, "ABANDONED\nattempt becomes n + 1", "#fee2e2")

    link(axes, (2.0, 2.75), (4.1, 2.75), INK, "leader picks the least\nloaded live worker", (0, 0.32))
    link(axes, (6.1, 2.75), (8.2, 2.75), INK, "worker's watch fires,\nit creates the lease", (0, 0.32))
    link(axes, (9.7, 2.3), (10.9, 1.2), INK, "CompleteTask with\nthe current attempt", (1.0, 0.2))
    link(axes, (9.0, 3.2), (3.3, 4.55), "#dc2626", curve=0.1)
    axes.text(
        7.65, 4.9,
        "Lease lost. Pod died: its session expired, so ZooKeeper removed the lease.\n"
        "Pod hung: expires_at passed, so the leader's reaper removed the lease.",
        ha="center", va="center", fontsize=8, color="#dc2626",
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0},
    )
    link(axes, (1.15, 4.1), (1.15, 3.2), INK, "requeued", (0.45, 0))
    link(axes, (8.6, 2.3), (6.1, 0.75), "#dc2626")
    axes.text(
        8.2, 0.55, "Hung worker wakes and reports attempt n,\nbut the task has moved on to n + 1",
        ha="center", va="center", fontsize=8, color="#dc2626",
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0},
    )

    save(figure, "task-lifecycle.png")


def main() -> None:
    deployment()
    microservices()
    task_lifecycle()


if __name__ == "__main__":
    main()
