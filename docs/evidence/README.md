# Evidence from Google Cloud

Captured during the live test on 5 November 2026, and the clean rehearsal on 6 November (files ending `-clean`). `docs/verification.md` summarises each file.

| File | Shows |
|---|---|
| `smoke-public-url.txt` | An order through the public load balancer reaching DISPATCHED |
| `k6-summary.json` | The k6 load test: 16,905 orders at 35 a second |
| `hpa-during-load.txt` | Both HPAs every 20 seconds during the load test |
| `chaos-under-load.txt` | The four ZooKeeper failure drills, injected under load |
| `reconciliation-database.json`, `reconciliation-cloud.json` | Orders, tasks, stock, Firestore, Cloud Storage and Flink totals agreeing |
| `gitops-rollout.txt` | A pushed commit rolled out by Argo CD |
| `gitops-self-heal.txt` | A manual change reverted by Argo CD |
| `observability.txt` | Prometheus targets, live metrics, Grafana data sources and dashboards |
| `central-logging-one-order.txt` | One order followed across services in Cloud Logging, with Flink's logs |

Still worth capturing at the demo, as screenshots, since these are visual:

- [ ] Argo CD showing all Applications Synced and Healthy
- [ ] Grafana Service RED and Warehouse KPIs during the load test
- [ ] Grafana ZooKeeper during a failover
- [ ] Grafana Central Logs following one `order_id`
- [ ] The Flink dashboard with the job running
- [ ] The budget and the Billing overview, showing spending inside the credit
