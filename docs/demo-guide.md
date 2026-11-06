# Demo Guide: Console Pages and Terminal Commands

This is the screen-by-screen plan for the live demo. Every requirement is shown in either the Google Cloud console or a terminal. For each one, it lists the page to open, what to point at, and the command to run.

[demo-runbook.md](demo-runbook.md) lists the questions each member should expect. [verification.md](verification.md) has the numbers from the rehearsal, if anyone asks what was measured.

- **Project:** `g18-warehouse-3c6b`, region `asia-south1`, zone `asia-south1-a`
- **Console:** <https://console.cloud.google.com/?project=g18-warehouse-3c6b>. Check that the project picker at the top shows `g18-warehouse-3c6b` before you start.
- **Length:** about 45 minutes, which is 10 for each member and a 5-minute opening. Bring-up starts 75 minutes before that.

## Resource names

| What | Name | Where in the console |
|---|---|---|
| GKE cluster | `warehouse`, 3 × n2-standard-2 | Kubernetes Engine → Clusters |
| Application namespace | `warehouse`. ZooKeeper runs here too, as `zookeeper-0..2` | Kubernetes Engine → Workloads |
| Managed Kafka cluster | `warehouse`, with topics `warehouse.orders`, `warehouse.task-events` and `warehouse.analytics.results` | Managed Service for Apache Kafka → Clusters |
| Dataproc cluster (Flink) | `warehouse-analytics` | Dataproc → Clusters |
| Cloud SQL | `warehouse-<suffix>`, PostgreSQL 16, private IP only. Database `warehouse`, with schemas `orders`, `inventory`, `scheduler` and `workers` | SQL |
| Firestore | Database `warehouse`, with collections `shipments`, `workers` and `analytics_zones` | Firestore |
| Data bucket | `g18-warehouse-3c6b-warehouse-data`, holding `manifests/` and `labels/` | Cloud Storage → Buckets |
| Other buckets | `g18-warehouse-3c6b-tfstate` (Terraform state), `g18-warehouse-3c6b-artifacts` (Flink JAR and function code), `g18-warehouse-3c6b-dataproc` | Cloud Storage → Buckets |
| Pub/Sub | Topic `warehouse-manifests`; push subscription `warehouse-dispatch`; dead-letter topic `warehouse-dispatch-dead-letter` and subscription `warehouse-dispatch-dead-letters` | Pub/Sub |
| Functions | `warehouse-dispatch` (Dispatch service), `billing-kill-switch` | Cloud Run |
| Images | Repository `warehouse` | Artifact Registry |
| Grafana dashboards | Service RED, Cluster Health, ZooKeeper, Warehouse KPIs, Central Logs | Grafana, through a port-forward |

## T−75 min: bring it up

Use a stable network. Managed Kafka alone takes 20 to 30 minutes. In the clean rehearsal, `make up` finished in 14 minutes and the whole stack was ready in 21, so 75 minutes leaves a margin for a slow Kafka or a retry.

```bash
cd warehouse-cloud
make preflight                 # vCPU quota free, against the 8 the stack needs
make up                        # prints the load balancer address at the end
gcloud container clusters get-credentials warehouse --zone asia-south1-a
export BASE_URL=http://$(kubectl -n warehouse get ingress warehouse -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
make smoke                     # one order, through to DISPATCHED
```

If `make up` stops on a DNS error or a 429, run `make up` again. Terraform picks up where it stopped.

## T−20 min: open everything

**Terminals.** Open five tabs, all in `warehouse-cloud`, with `BASE_URL` exported in each:

| Tab | Runs |
|---|---|
| 1 | Argo CD port-forward (below) |
| 2 | Grafana port-forward (below) |
| 3 | k6 |
| 4 | `kubectl -n warehouse get hpa -w` |
| 5 | Commands typed live |

```bash
# Tab 1: Argo CD on https://localhost:8443. User admin, password:
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8443:80

# Tab 2: Grafana on http://localhost:3000. User admin, password:
kubectl -n monitoring get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

# Tab 3: background load, so the dashboards have data when you get to them
k6 run -e BASE_URL=$BASE_URL -e PEAK_RPS=25 -e RAMP=1m -e HOLD=30m load-tests/orders.js
```

**Browser tabs, in demo order:**

1. Billing → Overview, Credits, and Budgets & alerts
2. Kubernetes Engine → Clusters → `warehouse`
3. Argo CD at `https://localhost:8443`
4. GitHub → the repository → Actions
5. Cloud Storage → `g18-warehouse-3c6b-warehouse-data`
6. Pub/Sub → Subscriptions
7. Cloud Run → `warehouse-dispatch`
8. Firestore → `warehouse` database
9. SQL → the `warehouse-…` instance
10. Managed Service for Apache Kafka → `warehouse`
11. Dataproc → `warehouse-analytics` → Web Interfaces. Run `./scripts/flink-ui.sh` to print the links.
12. Logging → Logs Explorer
13. Grafana at `http://localhost:3000`

**Final checks:**

- `kubectl -n argocd get applications`: every application is Synced and Healthy.
- `./scripts/chaos.sh status`: a scheduler leader is named, and ZooKeeper shows 1 leader and 2 followers.
- Open Grafana → Central Logs once with a real `order_id`. This dashboard is the only one not yet viewed in a browser. If its panels are empty, use the Logs Explorer query in section 4 instead.

---

## 1. Opening, 5 minutes (anyone)

**Diagram.** Show `docs/diagrams` or the design document's architecture figure. Name the 8 services and the three ways they talk: REST at the edge, gRPC between services, Kafka for events.

**Console → Kubernetes Engine → Workloads**, filtered to namespace `warehouse`. Every service, the three `zookeeper` pods and the three `job-scheduler` replicas show as OK.

**Terminal:**

```bash
kubectl -n warehouse get pods -o wide
curl -s -X POST $BASE_URL/v1/orders -H 'Content-Type: application/json' \
  -d '{"customer":"demo","lines":[{"sku":"SKU-A001","quantity":2}]}' | tee /tmp/order.json
export ORDER=$(python3 -c 'import json;print(json.load(open("/tmp/order.json"))["order_id"])')
sleep 15; curl -s $BASE_URL/v1/orders/$ORDER      # shows the tasks and the DISPATCHED status
```

Keep `$ORDER`. Sections 2 and 4 follow this same order.

---

## 2. Aakash: (a) Infrastructure as Code and (d) GitOps, 10 minutes

### (a) Terraform owns the infrastructure

**Terminal:**

```bash
terraform -chdir=infra/runtime plan          # expect: No changes. Your infrastructure matches the configuration.
ls infra/bootstrap infra/runtime
```

Explain the two stacks. `bootstrap` holds the long-lived pieces: state bucket, registry, Firestore, identities, budget and kill switch. `runtime` holds everything billed by the hour, which `make down` destroys.

**Console pages that Terraform created:**

| Page | Point at |
|---|---|
| **VPC network → VPC networks** | The `warehouse` network and its subnets, including `gke`, `data` and `serverless` |
| **Network services → Cloud NAT** | Nodes reach the internet without public IPs |
| **IAM & Admin → Service accounts** | One account per workload (`wh-…`, `dataproc-flink`, `dispatch-function`, …). No account has a key. |
| **IAM & Admin → Workload Identity Federation** | The GitHub pool. CI signs in with no stored secret. |
| **Cloud Storage → `g18-warehouse-3c6b-tfstate`** | The Terraform state, under `runtime/` |

### Spending is capped (part of a)

| Page | Point at |
|---|---|
| **Billing → Overview** and **Credits** | Credit remaining, and its end date |
| **Billing → Budgets & alerts** | The ₹22,000 budget across the whole billing account, counted before credits |
| **Cloud Run → `billing-kill-switch`** | The function that unlinks billing when the budget is reached |

```bash
make killswitch-check        # a self-test, which never turns billing off
```

A minute later, open **Cloud Run → billing-kill-switch → Logs**. It shows `can_unlink_billing: true`.

### (d) GitOps

**Argo CD (browser).** Show the `warehouse-root` app-of-apps tree, with platform, monitoring, ZooKeeper and services all Synced and Healthy.

**Rollout from Git.** Edit `ROUTE_STOPS` in `gitops/services/base/picking/deployment.yaml`, then:

```bash
git commit -am "Demo: change picking route stops" && git push
```

In Argo CD, click **Refresh** on the services application. Argo CD polls every 3 minutes, and Refresh skips the wait. Watch the new Picking ReplicaSet roll out. In the console, **Kubernetes Engine → Workloads → picking → Revision history** shows the new revision.

**Self-heal:**

```bash
kubectl -n warehouse scale deployment inventory --replicas=5
kubectl -n warehouse get deploy inventory -w       # back to 2 within seconds
```

Argo CD shows the drift and reverts it, because Git is the source of truth.

**CI.** Open **GitHub → Actions**. The `build` workflow builds images through Workload Identity Federation and commits the new tag to `gitops/`. The `test` workflow runs the full test suite. Then open **Artifact Registry → `warehouse`** to show the images and their tags.

---

## 3. Bhagavath: (b) Serverless and communication, and (g) Storage, 10 minutes

### (b) Packing to Dispatch, event-driven

Follow `$ORDER` along the chain:

| Step | Console page | Point at |
|---|---|---|
| 1 | **Cloud Storage → `g18-warehouse-3c6b-warehouse-data` → `manifests/<order_id>/`** | The manifest Packing wrote. Open it and show its JSON. |
| 2 | **Pub/Sub → Topics → `warehouse-manifests`** | Cloud Storage publishes a notification here for each upload |
| 3 | **Pub/Sub → Subscriptions → `warehouse-dispatch`** | A push subscription to the function, with dead-lettering after 5 attempts (`warehouse-dispatch-dead-letter`) |
| 4 | **Cloud Run → `warehouse-dispatch` → Metrics, then Logs** | Invocations under load, and `dispatch published` for the order |
| 5 | **Cloud Storage → `labels/<order_id>/`** | The shipping label the function wrote |
| 6 | **Firestore → `warehouse` database → `shipments` → `<order_id>`** | The shipment document |

**Idempotency.** Upload the same manifest again. The function runs a second time, but Firestore still has one shipment.

```bash
B=g18-warehouse-3c6b-warehouse-data
M=$(gcloud storage ls "gs://$B/manifests/$ORDER/")
gcloud storage cp "$M" /tmp/manifest.json && gcloud storage cp /tmp/manifest.json "$M"
```

In **Cloud Run → warehouse-dispatch → Logs**, the second delivery logs `duplicate manifest ignored`. The Firestore document is unchanged.

```bash
gcloud pubsub subscriptions pull warehouse-dispatch-dead-letters --limit 5    # empty: nothing failed 5 times
```

**Three communication styles.** REST is the `curl` above, through the load balancer: **Network services → Load balancing** shows the one GKE created. gRPC is Order Intake to Inventory, and the workers to the Scheduler. Kafka carries the events, shown next.

**Managed Service for Apache Kafka → Clusters → `warehouse`:** the 3 topics, their partitions, and the **Consumer groups** tab.

```bash
gcloud managed-kafka topics list --cluster warehouse --location asia-south1
gcloud managed-kafka consumer-groups list --cluster warehouse --location asia-south1
```

### (g) Three different stores, each for a reason

| Store | Holds | Show it |
|---|---|---|
| **Cloud SQL** (PostgreSQL) | Orders, stock, task queue: transactional data that needs joins | **SQL → instance → Overview**: private IP only, no public IP. Then the query below. |
| **Firestore** | Shipments, live workers, analytics windows: documents read by key | **Firestore → `warehouse`**: open `shipments`, `workers` and `analytics_zones` |
| **Cloud Storage** | Manifests and labels: files | The bucket above |

The database has no public IP, so query it from inside the cluster:

```bash
kubectl -n warehouse exec -i deploy/job-scheduler -- python - "$ORDER" <<'EOF'
import os, sys, psycopg
db = psycopg.connect(os.environ["DB_DSN"], autocommit=True)
print(db.execute("select * from scheduler.orders where order_id = %s", [sys.argv[1]]).fetchall())
print(db.execute("select task_type, status, count(*) from scheduler.tasks group by 1,2 order by 1,2").fetchall())
print(db.execute("select sku, on_hand, reserved from inventory.stock order by sku limit 5").fetchall())
EOF
```

---

## 4. Pranjal: (e) Flink and (h) Observability, 10 minutes

### (e) Flink on Dataproc

| Page | Point at |
|---|---|
| **Dataproc → Clusters → `warehouse-analytics`** | One node, image 2.2, scheduled to delete itself after 12 hours |
| **Dataproc → Jobs** | The Pig job that launched the Flink job (`submit-flink.sh`) |
| **Clusters → `warehouse-analytics` → Web Interfaces → YARN ResourceManager** | The `warehouse-analytics` application, RUNNING |
| That row's **ApplicationMaster** link | The Flink dashboard: the job graph (Kafka source → windows → Kafka sink), the **Checkpoints** tab (one every 30 s), and records in and out |

```bash
./scripts/flink-ui.sh                          # prints the Component Gateway links
curl -s $BASE_URL/v1/analytics/latest          # the newest 1-minute window for each zone
```

Explain the chain: task events → Flink, with 1-minute event-time windows and a 20-second watermark → `warehouse.analytics.results` → analytics-api → Firestore `analytics_zones`. Open **Firestore → `analytics_zones` → a zone → `windows`** to show the stored windows.

### (h) Observability

**Grafana (browser):**

| Dashboard | Point at |
|---|---|
| Service RED | Request rate, errors and latency for each service under the k6 load |
| Cluster Health | Node CPU and memory, pod counts |
| ZooKeeper | Ensemble state, connections, latency |
| Warehouse KPIs | Orders, picks, packs, dispatches, live workers, abandoned and reclaimed tasks |
| Central Logs | Paste `$ORDER`: its log lines from every service, the function and Flink |

**Console → Logging → Logs Explorer.** Paste this query, with your order id in place of `ord-…`:

```
((resource.type="k8s_container" AND resource.labels.namespace_name="warehouse")
 OR (resource.type="cloud_run_revision" AND resource.labels.service_name="warehouse-dispatch")
 OR (resource.type="cloud_dataproc_cluster" AND resource.labels.cluster_name="warehouse-analytics"))
AND jsonPayload.order_id="ord-…"
```

One order, followed through Order Intake, the Job Scheduler and the Dispatch function. Then show Flink's own logs:

```
resource.type="cloud_dataproc_cluster" AND resource.labels.cluster_name="warehouse-analytics" AND "Completed checkpoint"
```

**Kubernetes Engine → Clusters → `warehouse` → Observability** shows Google's own CPU and memory charts for the same cluster.

---

## 5. Aman: (c) Kubernetes, HPA and load, and (f) ZooKeeper, 10 minutes

### (c) Autoscaling under load

Raise the load. Stop the background k6 in tab 3 first.

```bash
k6 run -e BASE_URL=$BASE_URL -e PEAK_RPS=35 -e RAMP=1m -e HOLD=5m load-tests/orders.js     # tab 3
kubectl -n warehouse get hpa -w                                                            # tab 4
```

| Where | Point at |
|---|---|
| Tab 4 | `picking` and `order-intake` replicas rise from 2. In the rehearsal Picking peaked at 6 and Order Intake at 3. |
| **Kubernetes Engine → Workloads → picking → Details** | The Autoscaler section (2 to 6 pods, 60% CPU target), and the pod count going up |
| **Compute Engine → VM instances** | The 3 GKE nodes and the Dataproc node: 8 vCPUs in all, the cap Terraform enforces |
| Grafana → Warehouse KPIs | Live Workers rising: new Picking pods join ZooKeeper and get work with no scheduler change |

Offering 35 orders a second builds a backlog, which drains after k6 stops. Nothing is lost, and the check at the end proves it.

### (f) ZooKeeper coordination under failure

Keep the load running, with Grafana → ZooKeeper and Warehouse KPIs on screen.

```bash
./scripts/chaos.sh status          # scheduler leader, each ZooKeeper node's mode, and the HPAs
./scripts/chaos.sh kill-leader     # a standby scheduler takes over in about 11 s
./scripts/chaos.sh status
./scripts/chaos.sh kill-picker     # its lease vanishes; its task is abandoned, then reclaimed
./scripts/chaos.sh hang-picker     # stays connected but stops working; loses its lease after 20 s
./scripts/chaos.sh resume-picker   # reports late with an old attempt number and is fenced off
./scripts/chaos.sh kill-zookeeper  # 2 of 3 keep quorum and elect a new ZooKeeper leader
./scripts/chaos.sh status
```

After each step, point at **Kubernetes Engine → Workloads**, where the pod restarts, and at Warehouse KPIs, where orders keep flowing and the abandoned and reclaimed counts rise together.

---

## 6. Close, 5 minutes: prove nothing was lost

Wait for the backlog to drain. The Job Scheduler's `/v1/queue` is internal, so read it from inside its pod; it reaches 0 when the backlog is gone. Then run the totals:

```bash
kubectl -n warehouse exec deploy/job-scheduler -- python -c "import urllib.request as u; print(u.urlopen('http://localhost:8080/v1/queue').read().decode())"
kubectl -n warehouse exec -i deploy/job-scheduler -- python - <<'EOF'
import os, psycopg
db = psycopg.connect(os.environ["DB_DSN"], autocommit=True)
q = lambda s: db.execute(s).fetchone()[0]
print("orders:", q("select count(*) from scheduler.orders"),
      " dispatched:", q("select count(*) from scheduler.orders where dispatched_at is not null"),
      " unsent outbox:", q("select count(*) from scheduler.outbox"),
      " units still reserved:", q("select coalesce(sum(reserved),0) from inventory.stock"))
EOF
make smoke
```

Orders and dispatched should be equal, and the other two zero. In the rehearsal, 9,108 of 9,108 orders were dispatched. The query above has not yet been run in exactly this form. If a column name is wrong, use the reconciliation in [evidence/README.md](evidence/README.md).

## After the demo: tear down

```bash
make down
```

The script lists anything left over at the end. Expect errors on two network objects, the `serverless` subnet and the Cloud SQL peering: Google holds them for a while, they are free, and the next `make up` reuses them. Then check in the console:

- **Kubernetes Engine**, **Dataproc**, **SQL** and **Managed Kafka**: all empty.
- **Compute Engine → Disks**: no `pvc-…` disks left from ZooKeeper.
- **Billing → Reports**, filtered to today: what the demo cost. The stack costs about ₹86 an hour while it runs.

## Screenshots to take during the demo

These go into `docs/evidence/`:

- [ ] Argo CD: every application Synced and Healthy
- [ ] Grafana Service RED and Warehouse KPIs, under load
- [ ] Grafana ZooKeeper, during `kill-zookeeper`
- [ ] Grafana Central Logs, or Logs Explorer, following one `order_id`
- [ ] The Flink dashboard: job graph and checkpoints
- [ ] Billing: the budget, and the credit remaining
