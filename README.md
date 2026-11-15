# Smart Warehouse

A cloud-native fulfilment system for picking, packing and dispatch, with ZooKeeper coordinating work across simulated warehouse workers. It runs on Google Cloud. CS/SS G527 Cloud Computing, Group 18.

- Design: [docs/design-document.md](docs/design-document.md)
- What has been tested, and what has not: [docs/verification.md](docs/verification.md)
- Demo script: [docs/demo-runbook.md](docs/demo-runbook.md)
- Demo guide, page by page in the console and terminal: [docs/demo-guide.md](docs/demo-guide.md)
- Who owns what: [task_divison.txt](task_divison.txt)

## Repository layout

| Path | Contents |
|---|---|
| `services/` | The eight services. Python, except `analytics-flink`, which is Java. |
| `libs/warehouse_common/` | Shared logging, metrics, Kafka, ZooKeeper and Google Cloud code |
| `proto/`, `contracts/` | gRPC definitions and Kafka event schemas |
| `infra/` | Terraform: `bootstrap` runs once, `runtime` holds everything billed by the hour |
| `gitops/` | Kubernetes manifests and Argo CD configuration |
| `load-tests/` | k6 load test |
| `scripts/` | Bring-up, teardown, quota check and failure injection |
| `tests/` | Integration tests and stubbed tests of the ops scripts |

## Spending only the credit

The project runs on the group's Google Cloud credit: ₹28,663 (about $300) on a billing account that runs until 30 December 2026. That billing account is a **paid account**, so once the credit is used up Google charges the card behind it. Three things stop that from happening.

1. **A budget across the whole billing account.** The credit belongs to the account, and every project on it draws from the same credit, so `make bootstrap` creates a budget over all of them. It counts gross cost, before credits, from the day you start, and emails the billing administrators at 50%, 75%, 90% and 100%.
2. **A kill switch.** When that spending reaches `budget_amount`, a Cloud Run function unlinks billing from this project, which stops everything in it. Cost reports lag usage by hours, so set `budget_amount` well below the credit left: about ₹22,000 of the ₹26,988 left on 5 November leaves a margin of more than two days of running. `make killswitch-check` confirms the function holds the permission it needs, without using it.
3. **Nothing runs unattended.** `make down` destroys everything billed by the hour. Dataproc also deletes itself 12 hours after creation, in case `make down` is forgotten, and Terraform refuses to run more than 8 vCPUs at once.

Teammates get project roles but no billing role, so only the billing administrator can change how the project is paid for. Any other project on the same billing account, such as one created for the Gemini API, spends the same credit, so keep those idle.

The stack costs about **$0.90 (₹86) an hour** while it runs. These are Google's list prices for `asia-south1`, read from the Cloud Billing Catalog on 5 November 2026; Managed Kafka's compute units come from its pricing page. Left running around the clock it would use the whole credit in under two weeks, so run `make down` whenever you stop.

| Item | Size | $/hour |
|---|---|---|
| GKE nodes | 3 × n2-standard-2 | 0.350 |
| Managed Kafka | 3 vCPU and 3 GiB, the minimum: 2.1 compute units, plus its endpoints | 0.26 |
| Dataproc | 1 × n2-highmem-2, plus the Dataproc fee | 0.177 |
| Cloud SQL | db-g1-small, Enterprise edition, zonal, 10 GB HDD | 0.044 |
| Load balancer, NAT, IP addresses, disks | | about 0.07 |
| GKE management fee | One zonal cluster | 0, the GKE free tier pays it |
| Cloud Storage, Firestore, Pub/Sub, Cloud Run functions, Cloud Logging | Small usage | mostly inside the free tier |

The machines are N2 because this project has no E2 quota in `asia-south1`. `make preflight` checks that quota as well as the overall one.

## Run it locally, with no Google Cloud project

This needs Docker with Compose.

```bash
make local-up        # builds and starts everything
make smoke           # sends one order through the system
make load            # needs k6: sustained traffic against localhost
make local-down
```

Order Intake is on `localhost:8080`, Analytics on `localhost:8090`, and the Flink dashboard on `localhost:8081`.

Here is what stands in for each Google Cloud service:

| On Google Cloud | Locally |
|---|---|
| Managed Service for Apache Kafka | Kafka, without TLS or sign-in |
| Cloud SQL | PostgreSQL |
| Cloud Storage | [fake-gcs-server](https://github.com/fsouza/fake-gcs-server), which also publishes the upload notifications |
| Pub/Sub, Firestore | Google's own emulators |
| Cloud Run function | `local_runner.py`, which pulls from Pub/Sub and calls the same handler |
| Dataproc | Flink 1.17 JobManager and TaskManager containers |
| GKE, load balancer | Docker networking |

## Set up a computer

One script installs every tool this project uses, at the versions it was tested with: the Google Cloud CLI with `kubectl`, Terraform, kustomize, Helm, kubeconform, k6, Maven with a Java 11 JDK, Docker, and a Python environment in `.venv`.

**Linux** (Debian 13, or Ubuntu 24.04 and later):

```bash
./scripts/setup-linux.sh                # asks for your sudo password for apt and Docker
./scripts/setup-linux.sh --no-sudo      # only what goes in your home folder
```

**Windows** (10 version 2004 or later, or 11): the scripts here are bash, so they run in WSL, a real Ubuntu inside Windows. From PowerShell, in the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

The first run needs PowerShell opened as Administrator, and asks for a restart once WSL is on; run it again afterwards. It installs Ubuntu 24.04, copies the repository to `~/warehouse-cloud` inside it, and runs the Linux script there. Work from that copy, in the Ubuntu terminal.

Both are safe to run again. Each download is checked against a pinned SHA-256, and a tool that is missing, broken or a different version is replaced.

## Run the tests

```bash
source .venv/bin/activate
docker compose up -d zookeeper-1 zookeeper-2 zookeeper-3 postgres
make test
```

None of the tests need a Google Cloud project. Terraform is planned against mocked providers.

## Deploy to Google Cloud

### 1. Tools

Run the setup script for your system, from [Set up a computer](#set-up-a-computer).

### 2. Check the billing account

In the console, open **Billing**, choose the billing account, and look at:

- **Credits**: how much is left, and the end date. Set `budget_amount` well below what is left.
- **Overview**: whether it says *Paid account* or shows an **Activate** button for a Free Trial. On a paid account the card is charged once the credit is gone, which is why the budget and kill switch exist.
- **The currency.** Indian accounts are often in INR, and the budget must use the same currency.
- **Any banner asking to verify the account.** Verify it, or Google can suspend the account and stop everything.

### 3. Sign in

There are no key files anywhere in this project. You sign in as yourself, CI gets short-lived credentials from GitHub through Workload Identity Federation, and pods, Dataproc and the functions run as their own service accounts.

```bash
gcloud auth login                         # for gcloud commands
gcloud auth application-default login     # for Terraform and local Python clients
```

### 4. Create the project

```bash
gcloud projects create <project-id>
gcloud billing accounts list                                    # find the account id
gcloud billing projects link <project-id> --billing-account <account-id>
gcloud config set project <project-id>
gcloud auth application-default set-quota-project <project-id>
make preflight                                                  # vCPUs free, overall and for N2, against the 8 the stack needs
```

### 5. Create the long-lived resources

This makes the state bucket, image registry, Firestore, CI identity, team access, the budget, the kill switch, and the service accounts the running stack uses. Google limits how many service accounts a project may create per minute, so if it stops with that error, wait a minute and run `make bootstrap` again.

```bash
cp infra/bootstrap/terraform.tfvars.example infra/bootstrap/terraform.tfvars   # then edit it
make bootstrap
make killswitch-check
```

`make killswitch-check` sends the kill switch a self-test. A minute later its log should say `can_unlink_billing: true`. It never turns billing off itself.

### 6. Connect GitHub

Create the repository and push the code. Under **Settings → Secrets and variables → Actions → Variables**, set:

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | The project id |
| `GCP_REGION` | `asia-south1` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | The `ci_workload_identity_provider` output of `make bootstrap` |
| `GCP_CI_SERVICE_ACCOUNT` | The `ci_service_account` output |

These are variables, not secrets: none of them grants access on its own. Push to `main` and CI builds every image and commits its tag to `gitops/`.

### 7. Upload the Flink job

```bash
make flink-jar
```

### 8. Bring everything up

Managed Kafka alone takes 20 to 30 minutes, so allow an hour before a demo. If the run is cut off, for example by a lost network connection, run `make up` again: `scripts/recover.sh` finds what the first run created but did not record, adopts it with Terraform `import` blocks, and the apply creates only the rest. For a private repository, `make up` reads Argo CD's read-only deploy key from `../argocd_deploy_key`, outside the repository, or from `DEPLOY_KEY_FILE`.

```bash
cp infra/runtime/terraform.tfvars.example infra/runtime/terraform.tfvars   # then edit it
make up
BASE_URL=http://<printed-address> make smoke
```

Then open `http://<printed-address>/` in a browser. That is the **web front end**, served by Order Intake, with three views:

- **Shop**: 300 products in three departments, one per picking zone, with live stock and how many of each have been picked. Add them to a cart, choose a delivery speed (Standard, Express or Priority, which set the order's priority) and place the order.
- **Your orders**: every order placed from that browser, tracked live through picking, packing and dispatch, with the warehouse tasks, workers and attempt numbers behind each one.
- **Warehouse**: a map of the floor showing where each picker is working, the live workers with a history of pods joining and leaving, charts of Flink's last 30 windows, every recent order, and a load generator whose Rush hour setting makes the autoscaler add pickers.

It uses only the public API. To work on the page without Google Cloud, `make console` serves it with a fake API on `localhost:8099`; the fake API never runs in the cloud.

### 9. Tear it down when you finish

```bash
make down
```

The order inside `make down` matters, and the script handles it. Kubernetes created the load balancer and the ZooKeeper disks, so they must go first, or Terraform cannot delete the network and the disks keep billing. The script finishes by listing anything left. Expect it to stop with an error on two network objects, the `serverless` subnet and the Cloud SQL peering: Google holds them for a while after Cloud Run and Cloud SQL are deleted. They cost nothing, and the next `make up` reuses them.

### Teammates

Add each teammate's Google account email to `team_members` in `infra/bootstrap/terraform.tfvars` and run `make bootstrap` again. Each of them then runs, on their own machine:

```bash
gcloud auth login
gcloud config set project <project-id>
gcloud container clusters get-credentials warehouse --zone asia-south1-a
```

They can see everything, use `kubectl`, read logs and redeploy the Flink job, but they have no billing access. Changes reach the cluster through Git and Argo CD, so each member's work shows in their own commits.

### Checks to make on the first deployment

The documentation did not settle these. Each has a fallback.

| Check | If it fails |
|---|---|
| Managed Kafka creates in `asia-south1` for this project | Run Kafka in the cluster instead, which takes vCPUs from the same cap |
| Managed Kafka accepts a replication factor of 3 at its minimum size | Set `kafka_replication_factor = 2` |
| The Pig job starts Flink on Dataproc | Run `/tmp/submit-flink.sh` from an SSH session on the node |
| Flink writes checkpoints to `gs://` | Point `CHECKPOINT_DIR` in `flink.env` at `hdfs:///flink-checkpoints` |
| Flink logs appear in Cloud Logging as `yarn-userlogs` | Read them from the YARN UI |
| Pub/Sub push reaches the function | Check the function's logs and the dead-letter subscription |

## Useful commands on Google Cloud

```bash
kubectl -n warehouse get pods,hpa
./scripts/chaos.sh status

# Argo CD on localhost:8443. User admin, password from:
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
kubectl -n argocd port-forward svc/argocd-server 8443:80

# Grafana has its own public address. User admin; the password is grafana_admin_password
# in infra/runtime/terraform.tfvars, or a generated one if that is empty.
make grafana

# The YARN and Flink web UIs, through Dataproc's Component Gateway
./scripts/flink-ui.sh

# Manifests that failed five deliveries to the Dispatch function
gcloud pubsub subscriptions pull warehouse-dispatch-dead-letters --limit 5
```
