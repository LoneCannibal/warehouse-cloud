# Demo Runbook

Each member demonstrates their own two tasks and answers questions on them. The order below builds up: infrastructure first, then the application, then failures.

## Before the demo

Run `make up` at least an hour beforehand, because Managed Kafka alone takes 20 to 30 minutes. Then check:

```bash
BASE_URL=http://<load-balancer-ip> make smoke
./scripts/chaos.sh status
kubectl -n argocd get applications     # all should be Synced and Healthy
```

Open these in advance: Argo CD, Grafana, the Flink dashboard, the Logs Explorer in the Cloud console, and the Billing overview showing the remaining credit. Start a background load 10 minutes early so the dashboards have data:

```bash
k6 run -e BASE_URL=http://<load-balancer-ip> -e PEAK_RPS=30 load-tests/orders.js
```

## 1. Aakash: a. IaC and d. GitOps

**Show that Terraform owns the infrastructure.**

```bash
terraform -chdir=infra/runtime plan     # expect: No changes
```

Walk through `infra/runtime`. Point out that networks, clusters, databases, storage and IAM are all there. Then show `infra/bootstrap/budget.tf`: the budget, and the kill switch that turns billing off at the limit.

**Show that Git drives deployments.** Open Argo CD and show the app-of-apps tree. Then change something in Git, such as `ROUTE_STOPS` in `gitops/services/base/picking/deployment.yaml`, push, and watch Argo CD roll it out.

**Show self-heal.**

```bash
kubectl -n warehouse scale deployment inventory --replicas=5
```

Argo CD returns it to 2, because Git is the source of truth.

**Questions to expect:** Why two Terraform stacks? How does a manifest get an endpoint without containing it? Why does `make down` delete the Argo CD apps first? How do pods sign in to Google Cloud with no key file? How is spending kept inside the credit?

## 2. Bhagavath: b. Serverless and comms, and g. Storage

**Show the event chain.** Post an order and follow it.

```bash
ORDER=$(curl -s -X POST http://<load-balancer-ip>/v1/orders -H 'Content-Type: application/json' \
  -d '{"customer":"demo","lines":[{"sku":"SKU-A001","quantity":2}]}')
echo "$ORDER"
```

Show the manifest in the Cloud Storage data bucket under `manifests/`, the label under `labels/`, the `warehouse-dispatch` function's logs, and the document in the `shipments` collection of the `warehouse` Firestore database.

**Show idempotency.** Re-upload the same manifest to Cloud Storage. The function runs again, but Firestore still has one shipment.

**Show all three communication styles** on the microservices diagram: REST at the edge, gRPC for stock, and Kafka for events.

**Show the three stores.** Query one record in each and explain why that data is in that store.

**Questions to expect:** Why put Pub/Sub between Cloud Storage and the function? Why publish to Kafka before writing to Firestore? What does the dead-letter topic catch? How does the function reach Kafka's private addresses?

## 3. Pranjal: e. Flink and h. Observability

**Show the job running.** Run `./scripts/flink-ui.sh`, open the YARN ResourceManager link, then the warehouse-analytics application's Flink dashboard, and show the job graph.

**Show the results topic.** Query the windows through the API:

```bash
curl -s http://<load-balancer-ip>/v1/analytics/latest
```

Explain why queue depth needs state that outlives a window.

**Show observability.** In Grafana, go through Service RED, Cluster Health and Warehouse KPIs. Then open Central Logs, enter one `order_id`, and follow it across the services, the Dispatch function and Flink, all from Cloud Logging.

**Questions to expect:** What is a watermark, and why is it 20 seconds? Event time or processing time? What happens to a late event? What does a checkpoint store?

## 4. Aman: c. Kubernetes and HPA, and f. ZooKeeper

**Show the HPA scaling.** Raise the load, and watch in two terminals:

```bash
k6 run -e BASE_URL=http://<load-balancer-ip> -e PEAK_RPS=60 -e HOLD=8m load-tests/orders.js
kubectl -n warehouse get hpa -w
```

Show Live Workers rising in Warehouse KPIs. New Picking pods joined ZooKeeper and started receiving work with no scheduler change.

**Show ZooKeeper under failure.** Keep the load running, and keep Warehouse KPIs on screen.

```bash
./scripts/chaos.sh status

./scripts/chaos.sh kill-leader      # a standby takes over in about 11 seconds
./scripts/chaos.sh kill-picker      # its task is reclaimed, and the abandoned count rises
./scripts/chaos.sh hang-picker      # stays connected, loses its lease after 20 seconds
./scripts/chaos.sh resume-picker    # reports late, and the fenced count rises
./scripts/chaos.sh kill-zookeeper   # the ensemble elects a new leader and keeps serving
```

After each step, show that orders keep flowing. Finish with `make smoke` to show the system is healthy.

**Questions to expect:** Why does each follower watch only its predecessor? Why three nodes, not two or four? Why is an ephemeral lease not enough on its own? What does the attempt number protect against? Why not scale the Job Scheduler?

## After the demo

```bash
make down
```

Check that the script's final list is empty, and glance at the Billing overview to see what the session cost.
