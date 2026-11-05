# Evidence to capture on Google Cloud

Save these here during the first full deployment, then add them to section 15 of the design document.

- [ ] `terraform plan` reporting no changes
- [ ] Argo CD showing all Applications Synced and Healthy
- [ ] A commit rolling out through Argo CD, and a manual change being reverted
- [ ] `kubectl get hpa` during the load test, showing replicas above the minimum
- [ ] Grafana Service RED during the load test
- [ ] Grafana Cluster Health
- [ ] Grafana ZooKeeper during a failover
- [ ] Grafana Warehouse KPIs showing recovery after a killed picker
- [ ] Flink dashboard with the job running
- [ ] A record from `warehouse.analytics.results`
- [ ] Grafana Central Logs, or the Logs Explorer, following one `order_id`, including Flink and the Dispatch function
- [ ] The budget and the Billing overview, showing spending inside the credit
- [ ] The k6 end-of-test summary
