"""Draws the design document's diagrams into docs/diagrams.

They live as code so that a design change means a reviewable diff, not a redrawn
image. Needs matplotlib, which `make install` adds. Run with `make diagrams`.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import patheffects  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "diagrams"

INK = "#1f2933"
SERVICE = "#dbeafe"
STORE = "#dcfce7"
MANAGED = "#fef3c7"
SERVERLESS = "#fce7f3"
ZOOKEEPER = "#ede9fe"
TOPIC = "#a7f3d0"
PERSON = "#e2e8f0"
GROUP = "#f8fafc"

# Link types. Both architecture diagrams use these, and each type also has its own dash
# pattern, so the types stay apart in greyscale.
REST = "#2563eb"
GRPC = "#dc2626"
KAFKA = "#059669"
ZK_LINE = "#7c3aed"
EVENT = "#d97706"
DATA = "#64748b"
OBSERVE = "#0891b2"
DELIVERY = "#db2777"
PROVISION = "#111827"
DASHES = {
    REST: "-", GRPC: "-", KAFKA: "-", ZK_LINE: (0, (5, 2.5)), EVENT: (0, (7, 2, 1.5, 2)),
    DATA: (0, (1.5, 2)), OBSERVE: (0, (1.5, 2)), DELIVERY: "-", PROVISION: "-",
}
WIDTHS = {kind: 1.6 for kind in DASHES} | {KAFKA: 2.0}


def canvas(width: float, height: float, full: bool = False):
    """A blank drawing area in inch-sized units. With full, the axes fill the figure, so a
    unit is exactly an inch and dense diagrams get more room around their text."""
    if full:
        figure = plt.figure(figsize=(width, height))
        axes = figure.add_axes((0, 0, 1, 1))
    else:
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


def node(axes, x, y, width, height, title, subtitle=None, store=None, fill=SERVICE, pill=STORE,
         pill_ink="#166534"):
    """A box with a bold title, a line on where it runs, and the data it owns in a pill."""
    axes.add_patch(
        FancyBboxPatch(
            (x, y), width, height, boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=1.1, edgecolor=INK, facecolor=fill, zorder=2,
        )
    )
    middle = (y + (0.42 if store else 0) + y + height) / 2
    centre = x + width / 2
    lines = subtitle.count("\n") + 1 if subtitle else 0
    if subtitle:
        axes.text(centre, middle + 0.08 * lines + 0.06, title, ha="center", va="center", fontsize=9.5,
                  color=INK, weight="bold", zorder=3)
        axes.text(centre, middle - 0.1 * lines + 0.02, subtitle, ha="center", va="center", fontsize=7.5,
                  color=INK, zorder=3, linespacing=1.2)
    else:
        axes.text(centre, middle, title, ha="center", va="center", fontsize=9.5, color=INK, weight="bold",
                  zorder=3)
    if store:
        axes.add_patch(
            FancyBboxPatch(
                (x + 0.14, y + 0.1), width - 0.28, 0.28, boxstyle="round,pad=0.01,rounding_size=0.1",
                linewidth=0.8, edgecolor=pill_ink, facecolor=pill, zorder=3,
            )
        )
        axes.text(centre, y + 0.24, store, ha="center", va="center", fontsize=7, color=pill_ink, zorder=3)


def group(axes, x, y, width, height, title, fill=GROUP, title_x=None):
    """A dashed area with its title inside the top edge, at title_x if given."""
    axes.add_patch(
        FancyBboxPatch(
            (x, y), width, height, boxstyle="round,pad=0.02,rounding_size=0.1",
            linewidth=1.1, edgecolor="#94a3b8", facecolor=fill, linestyle="--", zorder=1,
        )
    )
    axes.text(
        x + 0.12 if title_x is None else title_x, y + height - 0.1, title, ha="left", va="top",
        fontsize=8.5, color="#475569", weight="bold", zorder=3,
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


def route(axes, points, kind, both=False, head=True):
    """A link through any number of points, styled by its type, with solid arrowheads.

    A white halo under the line leaves a visible gap wherever two links cross.
    """
    xs, ys = zip(*points)
    line = Line2D(xs, ys, color=kind, linewidth=WIDTHS[kind], linestyle=DASHES[kind], zorder=4,
                  solid_capstyle="butt", dash_capstyle="butt")
    line.set_path_effects([patheffects.Stroke(linewidth=WIDTHS[kind] + 3.5, foreground="white"),
                           patheffects.Normal()])
    axes.add_line(line)
    ends = ([(points[-2], points[-1])] if head else []) + ([(points[1], points[0])] if both else [])
    for tail, tip in ends:
        axes.add_patch(FancyArrowPatch(tail, tip, arrowstyle="-|>", mutation_scale=13, linewidth=1.2,
                                       color=kind, shrinkA=0, shrinkB=0, zorder=5))


def tag(axes, x, y, text, color, ha="center", rotation=0, size=7.5):
    axes.text(
        x, y, text, ha=ha, va="center", fontsize=size, color=color, rotation=rotation, zorder=6,
        linespacing=1.15, bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.2, "alpha": 0.95},
    )


def sample(axes, x, y, kind, length=0.5):
    axes.add_line(Line2D([x, x + length], [y, y], color=kind, linewidth=WIDTHS[kind], linestyle=DASHES[kind],
                         solid_capstyle="butt", dash_capstyle="butt"))


def key_row(figure, axes, x, y, entries, note=None):
    """A one-line key: a real sample of each link type, each label measured so none overlap."""
    renderer = figure.canvas.get_renderer()
    for kind, text in entries:
        sample(axes, x, y, kind)
        label = axes.text(x + 0.6, y, text, va="center", fontsize=8, color=INK)
        x = label.get_window_extent(renderer).transformed(axes.transData.inverted()).x1 + 0.45
    if note:
        axes.add_patch(FancyBboxPatch((x, y - 0.11), 0.5, 0.22, boxstyle="round,pad=0.01,rounding_size=0.08",
                                      linewidth=0.8, edgecolor="#166534", facecolor=STORE))
        axes.text(x + 0.6, y, note, va="center", fontsize=8, color=INK)


def key_column(axes, x, y, entries, step=0.3):
    """A key with one link type per row."""
    axes.text(x, y, "Link types", fontsize=8.5, color="#475569", weight="bold", va="center")
    for index, (kind, text) in enumerate(entries, start=1):
        row = y - index * step
        sample(axes, x, row, kind, length=0.45)
        axes.text(x + 0.55, row, text, va="center", fontsize=7.5, color=INK)


def save(figure, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT / name, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def deployment() -> None:
    """Where everything runs, and how traffic, data and deployments reach it."""
    figure, axes = canvas(14.4, 9.9, full=True)

    # Outside Google Cloud: clients, Terraform, GitHub, and the key.
    node(axes, 0.3, 8.2, 1.9, 0.75, "Clients and operators", "browser, k6", fill=PERSON)
    node(axes, 0.3, 7.25, 1.9, 0.7, "Terraform", "make bootstrap, make up", fill=PERSON)
    axes.text(1.25, 6.98, "creates the infrastructure,\ninstalls Argo CD", ha="center", va="center", fontsize=7,
              color=PROVISION, style="italic")
    group(axes, 0.15, 3.3, 2.2, 3.4, "GitHub")
    node(axes, 0.3, 5.5, 1.9, 0.8, "Repository", "code, Terraform,\nmanifests", fill=PERSON)
    node(axes, 0.3, 3.5, 1.9, 0.95, "GitHub Actions", "CI: builds images,\nreplaces the Flink job", fill=PERSON)
    key_column(axes, 0.2, 2.95, [
        (REST, "REST over HTTP"), (KAFKA, "Kafka"), (ZK_LINE, "ZooKeeper"),
        (EVENT, "Storage and Pub/Sub events"), (DATA, "storage access"), (OBSERVE, "observability"),
        (DELIVERY, "delivery: CI and GitOps"), (PROVISION, "provisioning"),
    ])

    group(axes, 2.6, 0.05, 11.65, 9.3, "Google Cloud project, asia-south1, paid for by the group's credit")

    group(axes, 2.85, 8.1, 8.75, 0.95, "Public edge", fill="#f1f5f9", title_x=6.55)
    node(axes, 3.0, 8.22, 2.1, 0.62, "External Application LB", "GKE Ingress: app, API, console", fill=MANAGED)
    node(axes, 9.35, 8.22, 2.1, 0.62, "External load balancer", "Grafana's LoadBalancer Service", fill=MANAGED)
    axes.text(7.22, 8.42, "Both created by Kubernetes,\nfrom Argo CD's manifests", ha="center", va="center", fontsize=7.5,
              color="#475569", style="italic")

    group(axes, 11.8, 4.2, 2.3, 4.85, "Access control", fill="#ffffff")
    node(axes, 11.95, 7.6, 2.0, 0.9, "IAM", "one service account\nper workload", fill="#ffffff")
    node(axes, 11.95, 6.3, 2.0, 0.9, "Workload Identity", "no key files", fill="#ffffff")
    node(axes, 11.95, 5.0, 2.0, 0.9, "Budget alerts", "and a billing\nkill switch", fill="#ffffff")

    group(axes, 2.85, 1.95, 8.75, 5.95, "VPC network: no public addresses, Private Google Access on every subnet",
          fill="#ffffff", title_x=4.3)
    group(axes, 3.0, 5.95, 8.45, 1.65, "Subnet gke: GKE zonal cluster, 3 x n2-standard-2, private nodes",
          fill="#eff6ff", title_x=4.3)
    node(axes, 3.15, 6.35, 1.85, 0.75, "7 Deployments", "HPA: Order Intake,\nPicking")
    node(axes, 5.75, 6.35, 1.75, 0.75, "ZooKeeper", "3 pods,\npersistent disks", fill=ZOOKEEPER)
    node(axes, 7.85, 6.35, 1.4, 0.75, "Argo CD", fill=MANAGED)
    node(axes, 9.55, 6.35, 1.75, 0.75, "Prometheus,\nGrafana", fill=MANAGED)

    group(axes, 5.6, 4.15, 5.85, 1.5, "Subnet data", fill="#f1f5f9")
    node(axes, 5.75, 4.35, 2.6, 0.75, "Managed Kafka", "3 vCPU, 3 GiB, SASL over TLS", fill=TOPIC)
    node(axes, 8.75, 4.35, 2.55, 0.75, "Dataproc 2.2, Flink 1.17", "one node; checkpoints to\nCloud Storage",
         fill=MANAGED)
    group(axes, 8.3, 2.1, 3.15, 1.7, "Subnet serverless", fill="#f1f5f9", title_x=9.05)
    node(axes, 8.5, 2.25, 2.75, 1.05, "Dispatch", "Cloud Run, Direct VPC egress;\nwrites shipments to Firestore",
         fill=SERVERLESS)
    node(axes, 5.75, 2.35, 2.2, 0.7, "Cloud NAT", "outbound traffic for nodes", fill=MANAGED)

    group(axes, 2.85, 0.25, 11.25, 1.5, "Google-managed services, reached privately", title_x=4.75)
    node(axes, 3.0, 0.4, 1.45, 0.95, "Cloud SQL", "PostgreSQL 16,\nprivate IP", fill=STORE)
    node(axes, 4.6, 0.4, 1.45, 0.95, "Artifact\nRegistry", fill=MANAGED)
    node(axes, 6.2, 0.4, 1.45, 0.95, "Firestore", "workers, shipments,\nanalytics_zones", fill=STORE)
    node(axes, 7.8, 0.4, 1.45, 0.95, "Cloud Storage", "data bucket,\ncheckpoints", fill=STORE)
    node(axes, 9.4, 0.4, 1.45, 0.95, "Pub/Sub", "push subscription,\ndead-letter topic", fill=MANAGED)
    node(axes, 11.0, 0.4, 1.45, 0.95, "Cloud Logging", "GKE, Dataproc,\nCloud Run", fill=MANAGED)
    node(axes, 12.6, 0.4, 1.45, 0.95, "Cloud\nMonitoring", fill=MANAGED)

    # REST over HTTP.
    route(axes, [(2.2, 8.55), (3.0, 8.55)], REST)
    tag(axes, 2.6, 8.72, "HTTP", REST)
    route(axes, [(1.25, 8.95), (1.25, 9.62), (10.4, 9.62), (10.4, 8.84)], REST)
    tag(axes, 6.0, 9.62, "HTTP: Grafana", REST)
    route(axes, [(4.05, 8.22), (4.05, 7.1)], REST)
    tag(axes, 3.95, 7.72, "routes by path", REST, ha="right")
    route(axes, [(10.4, 8.22), (10.4, 7.1)], REST)
    tag(axes, 10.5, 7.72, "dashboards", REST, ha="left")

    # ZooKeeper, Kafka.
    route(axes, [(5.0, 6.72), (5.75, 6.72)], ZK_LINE, both=True)
    tag(axes, 5.37, 7.0, "leases,\nelection", ZK_LINE, size=6.5)
    route(axes, [(4.45, 6.35), (4.45, 4.95), (5.75, 4.95)], KAFKA, both=True)
    tag(axes, 5.1, 5.2, "orders,\ntask events", KAFKA, size=7)
    route(axes, [(8.35, 4.72), (8.75, 4.72)], KAFKA, both=True)
    tag(axes, 8.55, 5.32, "Flink reads events,\nwrites results", KAFKA, size=6.5)
    route(axes, [(8.75, 3.3), (8.75, 3.95), (7.8, 3.95), (7.8, 4.35)], KAFKA)
    tag(axes, 7.0, 3.8, "SHIPMENT_DISPATCHED", KAFKA, size=7)

    # Cloud Storage and Pub/Sub events: the upload is what triggers Dispatch.
    route(axes, [(4.1, 6.35), (4.1, 1.85), (8.52, 1.85), (8.52, 1.35)], EVENT)
    tag(axes, 4.1, 3.9, "Packing uploads manifest", EVENT, rotation=90, size=7)
    route(axes, [(8.8, 0.4), (8.8, 0.32), (9.8, 0.32), (9.8, 0.4)], EVENT)
    tag(axes, 9.3, 0.32, "notification", EVENT, size=6.5)
    route(axes, [(10.12, 1.35), (10.12, 2.25)], EVENT)
    tag(axes, 10.12, 1.8, "HTTPS push", EVENT, size=7)

    # Storage access and observability.
    route(axes, [(3.6, 6.35), (3.6, 1.35)], DATA)
    tag(axes, 3.6, 3.1, "SQL, Firestore", DATA, rotation=90, size=7)
    route(axes, [(11.3, 6.72), (11.52, 6.72), (11.52, 1.85), (11.72, 1.85), (11.72, 1.35)], OBSERVE)
    route(axes, [(11.72, 1.85), (13.32, 1.85), (13.32, 1.35)], OBSERVE)
    tag(axes, 11.52, 3.0, "Grafana reads", OBSERVE, rotation=90, size=7)

    # Delivery: CI and GitOps.
    route(axes, [(0.85, 5.5), (0.85, 4.45)], DELIVERY)
    tag(axes, 0.85, 4.95, "push runs CI", DELIVERY, size=6.5)
    route(axes, [(1.65, 4.45), (1.65, 5.5)], DELIVERY)
    tag(axes, 1.65, 4.95, "commits\nimage tags", DELIVERY, size=6.5)
    route(axes, [(8.55, 7.1), (8.55, 7.27), (2.45, 7.27), (2.45, 5.9), (2.2, 5.9)], DELIVERY)
    tag(axes, 6.6, 7.27, "Argo CD pulls Git", DELIVERY, size=7)
    route(axes, [(8.55, 6.35), (8.55, 6.12), (4.8, 6.12), (4.8, 6.35)], DELIVERY)
    tag(axes, 6.9, 6.12, "deploys", DELIVERY, size=7)
    route(axes, [(2.2, 3.9), (2.45, 3.9), (2.45, 0.15), (5.32, 0.15), (5.32, 0.4)], DELIVERY)
    tag(axes, 3.9, 0.15, "pushes images", DELIVERY, size=7)

    # Provisioning.
    route(axes, [(2.2, 7.6), (2.6, 7.6)], PROVISION)

    save(figure, "deployment-architecture.png")


def microservices() -> None:
    """Every service-to-service link, labelled with its type and the calls or events it carries."""
    figure, axes = canvas(15.2, 14.9)
    axes.set_ylim(-1.0, 13.9)  # the key sits below the Kafka bar

    # Four columns, each (left edge, width), and the y of each row of boxes.
    c1, c2, c3, c4 = (0.75, 2.55), (4.6, 2.75), (8.7, 2.75), (12.3, 2.6)
    mid = {column: left + width / 2 for column, (left, width) in zip(("c1", "c2", "c3", "c4"), (c1, c2, c3, c4))}
    end = {column: left + width for column, (left, width) in zip(("c1", "c2", "c3", "c4"), (c1, c2, c3, c4))}
    top, core, high = 10.35, 7.4, 1.3

    # Public edge: one load balancer routes by path to four services.
    node(axes, c1[0], 12.8, c1[1], 0.8, "Clients", "Warehouse Console, k6", fill=PERSON)
    node(axes, 5.0, 12.8, 4.0, 0.8, "External Application LB", "GKE Ingress, routes by path", fill=MANAGED)
    route(axes, [(end["c1"], 13.2), (5.0, 13.2)], REST)
    tag(axes, 4.15, 13.42, "HTTP", REST)
    bus = 12.25
    axes.plot([mid["c1"], mid["c4"]], [bus, bus], color=REST, linewidth=2.4, zorder=4, solid_capstyle="round")
    axes.plot([7.0, 7.0], [12.8, bus], color=REST, linewidth=2.4, zorder=4)
    for x in (mid["c2"], 7.0, mid["c3"]):
        axes.plot(x, bus, "o", color=REST, markersize=4.5, zorder=5)
    for column, paths in (("c1", "/v1/stock"), ("c2", "/  /v1/orders  /v1/floor"), ("c3", "/v1/workers"),
                          ("c4", "/v1/analytics/*")):
        route(axes, [(mid[column], bus), (mid[column], top + high)], REST)
        tag(axes, mid[column] + 0.1, 11.95, paths, REST, ha="left")

    node(axes, c1[0], top, c1[1], high, "Inventory", "GKE, 2 replicas", "Cloud SQL: inventory")
    node(axes, c2[0], top, c2[1], high, "Order Intake", "GKE, HPA 2-6, serves the console", "Cloud SQL: orders")
    node(axes, c3[0], top, c3[1], high, "Worker Registry", "GKE, 2 replicas", "Cloud SQL + Firestore: workers")
    node(axes, c4[0], top, c4[1], high, "Analytics API", "GKE, 2 replicas", "Firestore: analytics_zones")
    node(axes, c2[0], core, c2[1], high, "Job Scheduler", "GKE, 3 replicas, 1 leader", "Cloud SQL: tasks, outbox")
    node(axes, c3[0], core, c3[1], high, "ZooKeeper", "3-node ensemble on GKE", "election, workers, offers, leases",
         fill=ZOOKEEPER, pill="#f5f3ff", pill_ink="#5b21b6")

    # Picking and Packing share the TaskWorker client, so their common links leave the group's border.
    group(axes, 0.55, 4.2, 7.05, 2.15, "")
    axes.text(0.7, 4.36, "Worker pods, each running the shared TaskWorker client", fontsize=8, color="#475569",
              weight="bold", va="center", zorder=3)
    node(axes, c1[0], 4.75, c1[1], 1.2, "Picking", "GKE, HPA 2-6, plans a route")
    node(axes, c2[0], 4.75, c2[1], 1.2, "Packing", "GKE, 2 replicas, chooses a box")
    node(axes, c3[0], 4.75, c3[1], 1.2, "Cloud Storage", "data bucket: manifests/, labels/", fill=STORE)
    node(axes, c3[0], 2.75, c3[1], 1.05, "Pub/Sub", "push subscription,\ndead-letter topic after 5 failures",
         fill=MANAGED)
    node(axes, c3[0], 0.95 + 0.05, c3[1], 1.15, "Dispatch", "Cloud Run function", "Firestore: shipments",
         fill=SERVERLESS)
    node(axes, c4[0], 2.65, c4[1], 1.25, "Flink job", "Dataproc, 1-min windows per zone",
         "checkpoints: Cloud Storage", fill=MANAGED)

    axes.add_patch(
        FancyBboxPatch(
            (0.15, 0.2 - 0.05), 14.75, 0.5, boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=1.1, edgecolor=INK, facecolor=TOPIC, zorder=2,
        )
    )
    axes.text(4.4, 0.4, "Kafka topic warehouse.task-events: every task lifecycle event", ha="center",
              va="center", fontsize=9, color=INK, weight="bold", zorder=3)

    # Order Intake.
    route(axes, [(c2[0], 11.0), (end["c1"], 11.0)], GRPC)
    tag(axes, (end["c1"] + c2[0]) / 2, 11.42, "gRPC\nReserveStock\nReleaseStock", GRPC, size=7)
    route(axes, [(5.4, top), (5.4, core + high)], KAFKA)
    tag(axes, 5.3, 9.52, "Kafka topic\nwarehouse.orders\nORDER_ACCEPTED", KAFKA, ha="right")
    route(axes, [(6.55, top), (6.55, core + high)], REST)
    tag(axes, 6.65, 9.88, "REST: order status,\ntasks, floor map", REST, ha="left")

    # Job Scheduler. Its outbox lane is the one link that has to cross another.
    route(axes, [(7.25, core + high), (9.0, top)], GRPC)
    tag(axes, 7.85, 9.15, "gRPC\nListAvailable", GRPC)
    route(axes, [(end["c2"], 8.1), (c3[0], 8.1)], ZK_LINE, both=True)
    tag(axes, (end["c2"] + c3[0]) / 2, 7.75, "election, epoch,\noffers, reaping", ZK_LINE, size=7)
    route(axes, [(c2[0], 8.4), (0.3, 8.4), (0.3, 0.65)], KAFKA, both=True)
    tag(axes, 0.3, 2.45, "Kafka: TASK_* events out, through the outbox;\nSHIPMENT_DISPATCHED in", KAFKA,
        rotation=90, size=7)
    route(axes, [(mid["c3"], core + high), (mid["c3"], top)], ZK_LINE)
    tag(axes, mid["c3"] + 0.1, 9.52, "ZooKeeper watch\non workers/", ZK_LINE, ha="left")

    # Workers.
    route(axes, [(mid["c1"], 5.95), (mid["c1"], top)], GRPC)
    tag(axes, mid["c1"], 9.4, "gRPC\nConfirmPick", GRPC)
    route(axes, [(5.4, 6.35), (5.4, core)], GRPC)
    tag(axes, 5.3, 6.88, "gRPC GetTask,\nCompleteTask", GRPC, ha="right")
    route(axes, [(7.1, 6.35), (9.0, core)], ZK_LINE, both=True)
    tag(axes, 8.05, 6.87, "membership, offers,\nephemeral lease", ZK_LINE, size=7)
    route(axes, [(3.95, 4.2), (3.95, 0.65)], KAFKA)
    tag(axes, 3.95, 2.45, "Kafka\nTASK_STARTED", KAFKA)

    # Packing to Dispatch: the upload is the trigger.
    route(axes, [(end["c2"], 5.35), (c3[0], 5.35)], EVENT)
    tag(axes, 8.03, 5.65, "upload\nmanifest", EVENT, size=7)
    route(axes, [(mid["c3"], 4.75), (mid["c3"], 3.8)], EVENT)
    tag(axes, mid["c3"] - 0.12, 4.27, "notification on\nnew manifests/ object", EVENT, ha="right", size=7)
    route(axes, [(mid["c3"], 2.75), (mid["c3"], 2.15)], EVENT)
    tag(axes, mid["c3"] - 0.12, 2.45, "HTTPS push, OIDC token,\nretries 5 s to 5 min", EVENT, ha="right", size=7)
    route(axes, [(end["c3"], 1.55), (11.9, 1.55), (11.9, 5.35), (end["c3"], 5.35)], DATA, both=True)
    tag(axes, 11.9, 3.45, "reads manifest, writes label", DATA, rotation=90, size=7)
    route(axes, [(mid["c3"], 1.0), (mid["c3"], 0.65)], KAFKA)
    tag(axes, mid["c3"] + 0.12, 0.82, "SHIPMENT_DISPATCHED", KAFKA, ha="left", size=7)

    # Analytics.
    route(axes, [(mid["c4"], 0.65), (mid["c4"], 2.65)], KAFKA)
    tag(axes, mid["c4"], 1.6, "consumes\nevery event", KAFKA)
    route(axes, [(mid["c4"], 3.9), (mid["c4"], top)], KAFKA)
    tag(axes, mid["c4"], 7.1, "Kafka topic\nwarehouse.analytics.results\nper-zone windows", KAFKA)

    key_row(figure, axes, 0.4, -0.35, [
        (REST, "REST over HTTP"), (GRPC, "gRPC"), (KAFKA, "Kafka"), (ZK_LINE, "ZooKeeper"),
        (EVENT, "Cloud Storage, Pub/Sub events"), (DATA, "storage access"),
    ], note="data the service owns")
    axes.text(0.4, -0.75, "Prometheus and Grafana scrape /metrics on every GKE pod; those links are not drawn.",
              va="center", fontsize=7.5, color="#475569", style="italic")
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
