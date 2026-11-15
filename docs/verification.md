# Verification

**Status: deployed to Google Cloud and tested live on 5 November 2026, then torn down.** `make up` brings it back in about 45 minutes.

The first part of this document covers the live deployment. The rest covers the local testing that came before it, on real software and Google's emulators, which still holds for the logic the cloud cannot easily show, such as a scheduler failover under controlled timing.

## Live on Google Cloud

Project `g18-warehouse-3c6b` in `asia-south1`, on the group's credit. Evidence files are in `docs/evidence/`.

| Check | Result | Evidence |
|---|---|---|
| Bootstrap | 46 resources: state bucket, registry, Firestore, CI identity, budget, kill switch | |
| Kill switch | Its self-test reported `can_unlink_billing: True`: it holds the permission to turn billing off | |
| CI | Tests pass on GitHub's runners. The image build signed in through Workload Identity Federation, pushed 7 images to Artifact Registry and committed their tags | |
| `make up` | 93 resources. Managed Kafka took 27 minutes; GKE, Dataproc and Cloud SQL about 6 minutes each | |
| Kafka topics | Created with replication factor 3 at the minimum cluster size | |
| Argo CD | All 5 Applications Synced and Healthy; it read the private repository with a read-only deploy key | |
| Pods | All 18 in `warehouse` Ready with no restarts: Cloud SQL over its private address, ZooKeeper quorum, Kafka sign-in through Workload Identity | |
| Public URL | An order through the load balancer reached DISPATCHED. `/chaos`, `/health`, `/metrics` and `/docs` are not exposed; `/` serves the web front end | `smoke-public-url.txt` |
| Warehouse Console (15 November) | Through the load balancer, `/` served the page, `/v1/stock` listed live stock and rejected a bad zone with 422, the page showed the real workers, stock and analytics, and `make smoke` passed. `/health`, `/metrics`, `/docs` and `/chaos/hang` still returned 404 | |
| Load test | 16,905 orders at 35 a second from India over the internet: failure rate 0.0001, p95 531 ms | `k6-summary.json` |
| HPA | Picking scaled from 2 to 6 pods and Order Intake from 2 to 4 within two minutes, then back down | `hpa-during-load.txt` |
| Failures under load | Scheduler leader killed: a standby took over. Picker killed: its tasks were reclaimed. Picker hung and resumed: its late results were fenced. ZooKeeper leader killed: a new leader took over and the node rejoined | `chaos-under-load.txt` |
| Consistency | Mid-drain, dispatched orders, Firestore shipments, Cloud Storage manifests and labels, and Flink's pack and dispatch counts were all exactly 11,515. Abandoned and reclaimed: 14 and 14 everywhere. Every pick confirmed exactly once | `reconciliation-*.json` |
| GitOps rollout | A pushed commit was synced by Argo CD 4 minutes 12 seconds later and rolled out 19 seconds after that | `gitops-rollout.txt` |
| Self-heal | A manual scale to 5 replicas was reverted to Git's 2 within 12 seconds | `gitops-self-heal.txt` |
| Flink on Dataproc | Started and replaced through the Pig job; checkpoints to Cloud Storage every 30 seconds; 128 windows reached Firestore through the results topic | |
| Metrics | 42 Prometheus targets up; Grafana has its Prometheus, Cloud Monitoring and Cloud Logging data sources and all five dashboards | `observability.txt` |
| Central logging | One order followed in Cloud Logging across Order Intake, the Job Scheduler and the Dispatch function; Flink's logs in the same place | `central-logging-one-order.txt` |
| Teardown | `make down` removed the load balancer and disks first, then all 93 resources | |

### Bugs the live deployment found

All are fixed and committed. None could show up locally or against mocks. The last was found on a later bring-up.

| Bug | How it showed up | Fix |
|---|---|---|
| The Kafka address had no port | The provider returns the host alone. Python's client assumes port 9092, but Flink's Java client refused it and restarted in a loop | Terraform adds `:9092` everywhere it passes the address |
| Flink's TaskManager never started | Dataproc's YARN counts vcores; one node offers 2, and the JobManager plus a 2-slot TaskManager asked for 3 | The TaskManager asks for 1 vcore |
| Packers starved under a deep backlog | Each assignment pass took the 100 oldest tasks of any type; a PICK backlog filled every pass while 5,036 PACK tasks waited | The pass takes the oldest tasks of each type, with a regression test that fails on the old code |
| Workers too slow for the HPA to notice | In the cloud each task waits on several round trips; two slots per pod left a backlog while CPU stayed under target | Four slots per picker and packer, rolled out through Git as the GitOps test |
| The Central Logs dashboard was empty | Grafana's Cloud Logging plugin fills in the project only for a query in its `query` field | The dashboard sets both fields, and still holds no project id |
| A plan wanted to rebuild Dataproc | Google adds three default scopes to the cluster's service account | The configuration lists them, as Google stores them |
| A plan wanted to change GKE | The temporary default pool's settings read back as the worker pool's | Terraform ignores them after creation |
| `make up` stopped on a rate limit | Creating 11 service accounts at once exceeded Google's per-minute limit | The accounts are created once in `infra/bootstrap`; `make up` only looks them up |
| Prometheus never started on one bring-up (14 November) | The Prometheus Operator started before Kubernetes had registered its CRDs, logged them as not installed, and switched off its Prometheus controller for good. Earlier runs had won the race | The CRDs come from their own Argo CD Application, `monitoring-crds`, in an earlier sync wave, and Argo CD now assesses Application health so the waves wait (its log shows the root waiting for `monitoring-crds`). `make up` also restarts the operator if Prometheus has not appeared |

Two lessons that are not bugs: a dropped network connection interrupted the first `make up`, because Terraform does not retry a failed DNS lookup, so run it on a stable network; and offering 35 orders a second is more than this stack completes, so a backlog builds and drains after the load stops.

### Clean rehearsal on 6 November 2026

A full demo-day run from nothing, with the final code and no manual steps: `make up`, the checks, then `make down`.

| Check | Result | Evidence |
|---|---|---|
| `make up` | 59 resources in 14 minutes with no errors, and no service accounts created. Argo CD synced all 5 Applications and all 18 pods ran 21 minutes after the start | |
| Flink | Started by Terraform's Dataproc job and checkpointing every 30 seconds, with no manual restart | |
| Public URL | The smoke test reached DISPATCHED. The first request returned 404 for about a minute while the load balancer rolled out its path rules | `smoke-clean-bringup.txt` |
| Load and failures | k6 ran 9,108 orders at 25 a second, failure rate 0.0002, p95 561 ms. Picking scaled to 6 pods and Order Intake to 3. The leader kill, picker kill, picker hang and resume, and ZooKeeper leader kill all recovered | `k6-summary-clean.json`, `hpa-during-load-clean.txt`, `chaos-under-load-clean.txt` |
| Packing fix | Packs kept completing while thousands of picks were still queued, and the backlog drained completely | `drain-clean.txt` |
| Consistency | 9,108 of 9,108 orders dispatched; every task done; every pick confirmed once; no stock left reserved; no outbox rows left. Firestore had 9,108 shipments and Cloud Storage 9,108 manifests and 9,108 labels. Flink's closed windows agreed: packs and dispatches equal, and abandoned and reclaimed both 15 | `reconciliation-database-clean.json`, `reconciliation-cloud-clean.txt` |

### Still unproven

- The Central Logs dashboard in a browser. Its query returns logs through Grafana's API in the form the browser sends.
- The billing account still asks to be verified. Until it is, Google may suspend it.

## 1. What ran, and as what

| Part | Used for testing | Kind | On Google Cloud |
|---|---|---|---|
| ZooKeeper | 3.9.7, three local nodes | Real | `zookeeper:3.9.6` on GKE |
| Kafka | 4.4.0, one broker, no TLS or sign-in | Real | Managed Service for Apache Kafka, SASL over TLS with Google sign-in |
| PostgreSQL | 16.15 | Real | Cloud SQL for PostgreSQL 16 |
| Flink | 1.17.2 standalone, on Java 11 | Real | Dataproc image 2.2: Flink 1.17.0 on YARN, Java 11 |
| k6 | The repository's script | Real | Same |
| All eight services | Separate processes on one machine, Python 3.13 | Real code | Containers, Python 3.12 |
| Cloud Storage | fake-gcs-server 1.56.1, publishing upload notifications to Pub/Sub | Emulator | Cloud Storage |
| Pub/Sub, Firestore | Google's emulators, from Google Cloud CLI 592.0.0 | Emulator | Pub/Sub, Firestore |
| Cloud Run function | `local_runner.py` pulls from Pub/Sub and calls the same handler | Stand-in | Pub/Sub push to the function |
| Terraform providers | Real schema of Google provider 8.10.0 for validation; `mock_provider` for plans | Real schema, mocked plan | Google Cloud |
| `gcloud`, `yarn`, `flink`, `kubectl` in the ops scripts | Recording stubs on `PATH` | Stub | Real tools |

## 2. Automated tests

All of these pass on the final code.

| Suite | Result | What it runs against |
|---|---|---|
| Shared library | 13 passed | Real gRPC servers; fake Google credentials for the Kafka token |
| Picking, Packing, Analytics API, Dispatch function | 6, 4, 4 and 10 passed | In-memory Firestore and Cloud Storage |
| Ops scripts, manifests and kill switch (`tests/scripts`) | 65 passed | Recording stubs and parsed YAML |
| Coordination (`tests/integration`) | 16 passed | Real three-node ZooKeeper and PostgreSQL |
| Flink job | 9 passed | Flink 1.17 MiniCluster on Java 11 |
| Terraform | 7 and 6 passed, for bootstrap and runtime | Mocked providers |
| Terraform validation | Both stacks valid, `fmt` clean | The real Google provider 8.10.0 schema |
| Kubernetes manifests | 31 valid, 0 invalid, 6 skipped | Kubernetes 1.36 schemas |
| Monitoring chart | kube-prometheus-stack 92.17.0 renders 101 objects with our values | Helm |
| Compose file | Parsed, 21 services, every image tag confirmed to exist | Real parser, never started |

- **Skipped manifests.** The 4 Argo CD Applications and 2 ServiceMonitors are custom resources with no schema available, so they were parsed but not checked against a schema.
- **What the Terraform tests pin.** The vCPU total stays within the cap of 8 and a larger plan is refused; GKE upgrades add no surge node; Cloud SQL is the Enterprise edition and zonal; Managed Prometheus is off; Managed Kafka is at its minimum size; Dataproc deletes itself; the budget counts cost before credits, across the whole billing account, from a fixed start date; the kill switch listens to the budget's topic and holds Project Billing Manager; teammates get no billing role; only this repository can use the CI identity.
- **One flaky run.** Once, the coordination suite did not exit after its tests; a rerun passed all 16 in 9 seconds. A ZooKeeper client thread left running at exit is the likely cause.
- **Services without unit tests.** Inventory, Order Intake, Job Scheduler and Worker Registry have no unit tests of their own. Job Scheduler and the worker code are covered by the coordination suite. All four are covered by the end-to-end checks in sections 3 and 4, which reconcile every order, task and unit of stock.

## 3. The Google Cloud version, on the emulators

k6 ramped to 15 orders per second and held for 60 seconds. Flink ran with 15-second windows.

| Check | Result |
|---|---|
| Orders accepted and dispatched | 1,070 of 1,070 |
| Firestore shipments | 1,070, one per order |
| Cloud Storage manifests and labels | 1,070 of each |
| Tasks | 3,065 of 3,065 done |
| Picks confirmed in Inventory and pick tasks done | 1,995 and 1,995 |
| Units still reserved | 0 |
| Outbox rows left unsent | 0 |
| Live workers mirrored to Firestore | 3 |
| Failed HTTP requests | 0, with a p95 of 6 ms |
| Flink windows read back from Firestore | 7 closed windows in 5 zones, 0 counters that disagree with Kafka |
| Tracebacks or errors in any service log | 0 |
| Duplicate deliveries or redeliveries at the function | 0 |

Before the run, a probe checked the emulated notification chain itself. An upload under `manifests/` produced one Pub/Sub message carrying `eventType=OBJECT_FINALIZE`, `bucketId` and `objectId`, the attributes the function reads, and an upload under `labels/` produced none.

## 4. Failure testing of the coordination core

This round ran before the move to Google Cloud, with real ZooKeeper, Kafka, PostgreSQL and Flink 1.20. k6 ramped to 25 orders per second and held for 75 seconds, with `ROUTE_STOPS=90`. Four failures were injected.

| Time | Failure injected | Result |
|---|---|---|
| 20.0 s | SIGKILL the scheduler leader | A standby was elected at 30.9 s, 10.8 s later, with epoch 2 |
| 40.9 s | SIGKILL `picking-0` while it held a lease | Its leases disappeared and the work was reclaimed |
| 54.9 s | Restart `picking-0` | It rejoined and received work with no scheduler change |
| 59.9 s | Hang `picking-1`, session alive, no renewals | The reaper expired its leases |
| 71.9 s | Resume `picking-1` | Its two late results were rejected by the attempt number |
| 75.9 s | SIGKILL the ZooKeeper leader node | A new ZooKeeper leader took over, and quorum held at 2 of 3 |
| 96.1 s | k6 finished | The backlog was already empty |

| Check | Result |
|---|---|
| Orders accepted and dispatched | 2,145 of 2,145, no duplicate shipments |
| Units ordered and removed from stock | 8,078 and 8,078, with 0 left reserved |
| Failed HTTP requests | 0, with a p95 of 8 ms |
| Longest pause in order flow | 10.8 s, the failover itself |
| Abandoned and reclaimed | 8 and 8, on 6 tasks, highest attempt 3 |
| Late results fenced | 2 |
| Flink windows compared with Kafka | All 8 closed windows match exactly, 0 late events dropped |
| Tracebacks in any service log | 0 |

A separate run used `ROUTE_STOPS=900` to keep one task in progress. After a SIGKILL mid-task, the lease outlived the process and disappeared at 10.5 s. The task was requeued as attempt 2 at 10.5 s and completed by `picking-1` at 13.4 s, and its pick was confirmed exactly once.

## 5. CPU measurements for the HPAs

The HPAs need a cluster, so the load that drives them was measured instead.

| Service | Setting | CPU |
|---|---|---|
| Order Intake | 5 to 22 requests per second | 3.2 to 5.1 ms per request |
| Picking | `ROUTE_STOPS` 40, 90, 150, 250 | 4, 18, 48, 135 ms per task |

Section 9 of the design document works out the request sizes from these figures.

## 6. Dashboards

In the first round, all 34 Prometheus queries parsed against a real Prometheus with every local target up, and 19 returned live data. The other 15 need Kubernetes metrics or the ZooKeeper exporter. Those dashboards did not change in the move. The Central Logs dashboard was rewritten for Cloud Logging and has not run against it.

## 7. Bugs found by testing

| Bug | How it showed up | Fix |
|---|---|---|
| Provider 7.x does not export Managed Kafka's bootstrap address | Validation against the real provider schema failed before any apply | Moved to Google provider 8.10 |
| A new scheduler leader sat idle for 30 s after winning the election | Order flow stopped for about 40 s, though failover took 11 s | Kafka group session timeout lowered from 45 s to 10 s |
| A Terraform `count` depended on an apply-time value | Would have failed the first real apply | Count now depends on a value known at plan time |
| A consumer missed topics created after it subscribed | Events unread for 5 minutes | Metadata refresh every 30 s |
| ZooKeeper `ConnectionLoss` escaped election and lease renewal | Threads died when a ZooKeeper node was killed | Retries and guards |
| Worker race on reclaimed offers | A stale offer could run | The worker checks the offer and attempt first |
| Assignment polled every 200 ms | Each two-slot worker was capped at 10 tasks per second | The assigner wakes when a lease changes |
| `min()` broke ties the same way every time | All work went to one pod | Random tie-break |
| No gRPC retry | Finished work was lost during failover | Retry on `UNAVAILABLE` |
| Reclaimed tasks queued behind the backlog | Recovery took 7.8 s | Reclaimed tasks go first, which cut it to milliseconds |
| Order Intake's CPU request was too large | The HPA could never reach its target | Request lowered from 250m to 100m |
| Flink used Kafka timestamps | Event time was wrong after a delay | `TaskEventSchema` reads the event's own time |
| A 10-second watermark | Events held back during failover arrived late | Raised to 20 s |
| Flink Jackson conflict and missing slf4j | Job failed at startup | `jackson-bom`, and slf4j as provided |
| New order reported as scheduler down | A 404 for a task list not yet created | Reported as `ACCEPTED` |
| `down.sh` could abort partway through teardown | `pipefail` on an empty result | Teardown continues |
| Error-rate panel showed No data | A healthy service has no error series | `or 0 *` in the query |

## 8. Not verified locally

Everything this section once listed as unproven, from Kafka sign-in to the HPA and Argo CD, has since run on Google Cloud: see the live section at the top. What remains unproven is listed there under "Still unproven".
