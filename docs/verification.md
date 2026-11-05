# Verification

**Status: validated locally. Not yet deployed to Google Cloud.**

The application logic, coordination and stream processing ran on real software, and the Google Cloud services ran on Google's own emulators or close stand-ins. Nothing here has touched a Google Cloud project, so no result below proves how GKE, Dataproc, Managed Kafka, Cloud SQL or Cloud Run behave. Section 8 lists what is still unproven and what to check first.

Testing ran in two rounds. The first, before the move from AWS to Google Cloud, failure-tested the coordination core. The second tested the Google Cloud version. The scheduling, ZooKeeper, fencing and outbox code did not change between them; the storage clients, the serverless entry point and Kafka sign-in did.

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
| Ops scripts, manifests and kill switch (`tests/scripts`) | 63 passed | Recording stubs and parsed YAML |
| Coordination (`tests/integration`) | 15 passed | Real three-node ZooKeeper and PostgreSQL |
| Flink job | 9 passed | Flink 1.17 MiniCluster on Java 11 |
| Terraform | 6 and 6 passed, for bootstrap and runtime | Mocked providers |
| Terraform validation | Both stacks valid, `fmt` clean | The real Google provider 8.10.0 schema |
| Kubernetes manifests | 31 valid, 0 invalid, 6 skipped | Kubernetes 1.36 schemas |
| Monitoring chart | kube-prometheus-stack 92.17.0 renders 101 objects with our values | Helm |
| Compose file | Parsed, 21 services, every image tag confirmed to exist | Real parser, never started |

- **Skipped manifests.** The 4 Argo CD Applications and 2 ServiceMonitors are custom resources with no schema available, so they were parsed but not checked against a schema.
- **What the Terraform tests pin.** The vCPU total stays within the cap of 8 and a larger plan is refused; GKE upgrades add no surge node; Cloud SQL is the Enterprise edition and zonal; Managed Prometheus is off; Managed Kafka is at its minimum size; Dataproc deletes itself; the budget counts cost before credits, across the whole billing account, from a fixed start date; the kill switch listens to the budget's topic and holds Project Billing Manager; teammates get no billing role; only this repository can use the CI identity.
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

## 8. Not verified

| Area | State | What to do first on Google Cloud |
|---|---|---|
| Any real Google Cloud API | Nothing has been applied | `make bootstrap`, then `make up` |
| The vCPU quota | Read from the project: 32 across it, 100 in `asia-south1`, 200 for N2 there, and 0 for E2 there, which is why the stack uses N2 | `make preflight` repeats the check before every `make up` |
| Managed Kafka in this project | Never created | Apply once. The fallback is Kafka on GKE, which takes vCPUs from the same cap |
| Replication factor 3 at Managed Kafka's minimum size | Unconfirmed | Fall back to `kafka_replication_factor = 2` |
| Kafka sign-in | The token builder matches Google's sample and is unit-tested; the Java handler loads; neither has met a real broker | Watch the services' and Flink's logs for authentication errors after `make up` |
| TLS trust for Kafka | `ssl.ca.location=probe` relies on the image's CA bundle | Same as above |
| The Pig job that starts Flink | Script tested with stubs only | Check the Dataproc job's output and the YARN application list |
| Flink checkpoints on `gs://` | Unconfirmed on Dataproc | Fall back to `hdfs:///flink-checkpoints` in `flink.env` |
| Flink logs in Cloud Logging | Documented as `yarn-userlogs`, not seen | Search Cloud Logging for the warehouse-analytics application |
| Pub/Sub push to the function, and dead letters | Ran on a pull-based stand-in | Run the smoke test and read the function's logs |
| The function reaching Kafka over Direct VPC egress | Unconfirmed, including DNS for the bootstrap address | Same as above |
| Workload Identity for pods | Configured, never exercised | Pods that cannot sign in fail at start-up with permission errors |
| GKE Ingress | Paths and health check follow GKE's documented rules; never created | `make up` waits for its address; then `make smoke` |
| Grafana's Cloud Logging data source | The dashboard's query format is unconfirmed | Open Central Logs; adjust the query in Grafana if empty |
| Budget and kill switch | Plans tested with mocks | `make killswitch-check` confirms the permission without turning billing off |
| HPA scale-out | Never ran on a cluster | Run the k6 test and watch `kubectl get hpa` |
| Argo CD | Manifests render; no sync has happened | Check every Application is Synced and Healthy |
| Container images | Never built, as this machine has no Docker | The first CI run |
| Docker Compose | Parsed, never started | `make local-up` on a machine with Docker |
| GitHub Actions | Parsed as YAML, never run | The first push |
| Version differences | Python 3.13 here, 3.12 in images. Kafka 4.4 here; Managed Kafka defaults to 3.7. ZooKeeper 3.9.7 here, 3.9.6 in the image. Flink 1.17.2 here, 1.17.0 on Dataproc | Covered by the checks above |
| Cost | About $0.90 an hour, from Google's list prices for `asia-south1`; sustained-use discounts and real data volumes are not counted | The budget emails and the Billing overview after the first session |

All local runs were on one machine, so these figures show correct behaviour under failure, not production capacity. Repeat the load and failure tests on Google Cloud with `load-tests/orders.js` and `scripts/chaos.sh`, as set out in `docs/demo-runbook.md`, and save the results listed in `docs/evidence/README.md`.
