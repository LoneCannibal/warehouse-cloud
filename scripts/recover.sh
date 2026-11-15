#!/usr/bin/env bash
# Lets `make up` and `make down` carry on after a run was cut off, for example when this
# computer lost its network connection half-way through.
#
#   ./scripts/recover.sh              clear a stale lock, then adopt leftover resources
#   ./scripts/recover.sh --lock-only  only clear a stale lock (used by make down)
#
# Terraform records a resource in its state only after Google confirms it was created.
# If the connection drops in between, the resource exists but Terraform does not know
# it, and the next apply fails with "already exists". This script finds such leftovers
# and writes an `import` block for each into infra/runtime/recovered_imports.tf, so the
# next apply adopts them instead of creating them again. Import blocks, unlike
# `terraform import`, work even while the GKE cluster is still missing from the state.
#
# It also:
#   - waits for a leftover that Google is still creating, before deciding what to do
#   - deletes a leftover Dataproc cluster, which Terraform cannot import, so the apply
#     recreates it
#   - deletes GKE's temporary default node pool if the cut came before Terraform did
#   - releases Terraform's state lock if a cut-off run on this computer left it behind
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-south1}"
ZONE="${ZONE:-asia-south1-a}"
STACK=infra/runtime
OUT="${RECOVERED_IMPORTS:-$STACK/recovered_imports.tf}"
BACKEND="${BACKEND_HCL:-$STACK/backend.hcl}" # written by make backend; Git ignores it
WAIT_ROUNDS="${WAIT_ROUNDS:-160}" # 15 s each: Managed Kafka can take 30 minutes

note() { printf '   %s\n' "$*"; }

# ---------------------------------------------------------------- the state lock

clear_stale_lock() {
  local bucket lock who id
  bucket=$(sed -n 's/^bucket *= *"\(.*\)"/\1/p' "$BACKEND" 2>/dev/null || true)
  [ -n "$bucket" ] || return 0
  lock=$(gcloud storage cat "gs://$bucket/runtime/default.tflock" 2>/dev/null || true)
  [ -n "$lock" ] || return 0
  if pgrep -f "terraform -chdir=$STACK (apply|destroy|plan)" > /dev/null 2>&1; then
    echo "Terraform is already running on this computer. Wait for it to finish." >&2
    exit 1
  fi
  who=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("Who",""))' "$lock")
  id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("ID",""))' "$lock")
  if [ "$who" = "$(id -un)@$(hostname)" ]; then
    echo "Releasing the state lock left by an interrupted run on this computer."
    terraform -chdir="$STACK" force-unlock -force "$id"
  else
    echo "Terraform's state is locked by $who. If no run is in progress there, release it with:" >&2
    echo "  terraform -chdir=$STACK force-unlock $id" >&2
    exit 1
  fi
}

clear_stale_lock
[ "${1:-}" = "--lock-only" ] && exit 0

# ------------------------------------------------------------ leftover resources

STATE=$(terraform -chdir="$STACK" state list 2>/dev/null || true)
in_state() { printf '%s\n' "$STATE" | grep -qxF -- "$1"; }

: > "$OUT.tmp"
ADOPTED=0

adopt() { # address import-id
  printf 'import {\n  to = %s\n  id = "%s"\n}\n\n' "$1" "$2" >> "$OUT.tmp"
  ADOPTED=$((ADOPTED + 1))
  note "adopting $1"
}

# Prints a resource's status, waiting while Google is still creating it. Prints nothing
# if the resource does not exist. Arguments: the busy statuses, then the probe command.
settled_status() {
  local busy=$1 status
  shift
  for _ in $(seq 1 "$WAIT_ROUNDS"); do
    status=$("$@" 2>/dev/null || true)
    if [ -z "$status" ] || [[ " $busy " != *" $status "* ]]; then
      printf '%s' "$status"
      return 0
    fi
    note "waiting: $status" >&2
    sleep 15
  done
  printf '%s' "$status"
}

exists() { [ -n "$("$@" 2>/dev/null || true)" ]; }

echo "Looking for resources an interrupted run left behind..."

# GKE first: Kubernetes resources can only be checked once the cluster is reachable.
if ! in_state google_container_cluster.this; then
  status=$(settled_status "PROVISIONING RECONCILING" \
    gcloud container clusters describe warehouse --zone "$ZONE" --format='value(status)')
  case "$status" in
    "") ;;
    RUNNING)
      adopt google_container_cluster.this "projects/$PROJECT/locations/$ZONE/clusters/warehouse"
      # Terraform removes this pool as the last step of creating the cluster.
      if exists gcloud container node-pools describe default-pool --cluster warehouse --zone "$ZONE" --format='value(name)'; then
        note "deleting GKE's temporary default node pool"
        gcloud container node-pools delete default-pool --cluster warehouse --zone "$ZONE" --quiet
      fi
      ;;
    *)
      note "the GKE cluster is $status; deleting it so it is created again"
      gcloud container clusters delete warehouse --zone "$ZONE" --quiet
      ;;
  esac
fi

if ! in_state google_container_node_pool.workers; then
  status=$(settled_status "PROVISIONING RECONCILING" \
    gcloud container node-pools describe workers --cluster warehouse --zone "$ZONE" --format='value(status)')
  case "$status" in
    "") ;;
    RUNNING) adopt google_container_node_pool.workers "projects/$PROJECT/locations/$ZONE/clusters/warehouse/nodePools/workers" ;;
    *)
      note "the node pool is $status; deleting it so it is created again"
      gcloud container node-pools delete workers --cluster warehouse --zone "$ZONE" --quiet
      ;;
  esac
fi

if ! in_state google_managed_kafka_cluster.this; then
  status=$(settled_status "CREATING" \
    gcloud managed-kafka clusters describe warehouse --location "$REGION" --format='value(state)')
  [ "$status" = ACTIVE ] &&
    adopt google_managed_kafka_cluster.this "projects/$PROJECT/locations/$REGION/clusters/warehouse"
fi
for topic in warehouse.orders warehouse.task-events warehouse.analytics.results; do
  if ! in_state "google_managed_kafka_topic.this[\"$topic\"]" &&
    exists gcloud managed-kafka topics describe "$topic" --cluster warehouse --location "$REGION" --format='value(name)'; then
    adopt "google_managed_kafka_topic.this[\"$topic\"]" "projects/$PROJECT/locations/$REGION/clusters/warehouse/topics/$topic"
  fi
done

# The instance name ends in a random suffix, which is in the state once it exists.
SQL_SUFFIX=$(terraform -chdir="$STACK" state show -no-color random_id.sql 2>/dev/null |
  sed -n 's/^ *hex *= *"\(.*\)"/\1/p' || true)
if [ -n "$SQL_SUFFIX" ]; then
  INSTANCE="warehouse-$SQL_SUFFIX"
  if ! in_state google_sql_database_instance.this; then
    status=$(settled_status "PENDING_CREATE" \
      gcloud sql instances describe "$INSTANCE" --format='value(state)')
    [ "$status" = RUNNABLE ] &&
      adopt google_sql_database_instance.this "projects/$PROJECT/instances/$INSTANCE"
  fi
  if ! in_state google_sql_database.warehouse &&
    exists gcloud sql databases describe warehouse --instance "$INSTANCE" --format='value(name)'; then
    adopt google_sql_database.warehouse "projects/$PROJECT/instances/$INSTANCE/databases/warehouse"
  fi
  if ! in_state google_sql_user.warehouse &&
    exists gcloud sql users list --instance "$INSTANCE" --filter=name=warehouse --format='value(name)'; then
    adopt google_sql_user.warehouse "$PROJECT/$INSTANCE/warehouse"
  fi
fi

# Terraform cannot import a Dataproc cluster, so a leftover one is deleted and recreated.
if ! in_state google_dataproc_cluster.analytics; then
  status=$(settled_status "CREATING" \
    gcloud dataproc clusters describe warehouse-analytics --region "$REGION" --format='value(status.state)')
  if [ -n "$status" ]; then
    note "deleting the leftover Dataproc cluster, which Terraform cannot adopt"
    gcloud dataproc clusters delete warehouse-analytics --region "$REGION" --quiet
  fi
fi

if ! in_state google_cloudfunctions2_function.dispatch; then
  status=$(settled_status "DEPLOYING" \
    gcloud functions describe warehouse-dispatch --region "$REGION" --format='value(state)')
  case "$status" in
    "") ;;
    ACTIVE) adopt google_cloudfunctions2_function.dispatch "projects/$PROJECT/locations/$REGION/functions/warehouse-dispatch" ;;
    *)
      note "the Dispatch function is $status; deleting it so it is created again"
      gcloud functions delete warehouse-dispatch --region "$REGION" --quiet
      ;;
  esac
fi

for bucket in data:warehouse-data dataproc:dataproc; do
  name="${bucket%%:*}" suffix="${bucket#*:}"
  if ! in_state "google_storage_bucket.$name" &&
    exists gcloud storage buckets describe "gs://$PROJECT-$suffix" --format='value(name)'; then
    adopt "google_storage_bucket.$name" "$PROJECT-$suffix"
  fi
done

for topic in manifests:warehouse-manifests dispatch_dead_letter:warehouse-dispatch-dead-letter; do
  name="${topic%%:*}" id="${topic#*:}"
  if ! in_state "google_pubsub_topic.$name" &&
    exists gcloud pubsub topics describe "$id" --format='value(name)'; then
    adopt "google_pubsub_topic.$name" "projects/$PROJECT/topics/$id"
  fi
done
for sub in dispatch:warehouse-dispatch dispatch_dead_letters:warehouse-dispatch-dead-letters; do
  name="${sub%%:*}" id="${sub#*:}"
  if ! in_state "google_pubsub_subscription.$name" &&
    exists gcloud pubsub subscriptions describe "$id" --format='value(name)'; then
    adopt "google_pubsub_subscription.$name" "projects/$PROJECT/subscriptions/$id"
  fi
done

# Kubernetes objects and Helm releases, if the cluster can be reached.
if exists gcloud container clusters describe warehouse --zone "$ZONE" --format='value(name)' &&
  gcloud container clusters get-credentials warehouse --zone "$ZONE" > /dev/null 2>&1; then
  k8s() { # address namespace kind name
    if ! in_state "$1" && exists kubectl ${2:+-n "$2"} get "$3" "$4" -o name; then
      adopt "$1" "${2:+$2/}$4"
    fi
  }
  for ns in argocd monitoring warehouse; do
    k8s "kubernetes_namespace_v1.this[\"$ns\"]" "" namespace "$ns"
  done
  k8s kubernetes_config_map_v1.infra_outputs warehouse configmap infra-outputs
  k8s kubernetes_secret_v1.database warehouse secret warehouse-database
  k8s "kubernetes_secret_v1.argocd_repository[0]" argocd secret warehouse-repository
  for sa in order-intake job-scheduler worker-registry picking packing analytics-api; do
    k8s "kubernetes_service_account_v1.workload[\"$sa\"]" warehouse serviceaccount "$sa"
  done
  k8s 'kubernetes_service_account_v1.workload["grafana"]' monitoring serviceaccount grafana
  for release in argocd:argocd root_application:warehouse-root; do
    name="${release%%:*}" id="${release#*:}"
    if ! in_state "helm_release.$name" &&
      exists kubectl -n argocd get secret -l "owner=helm,name=$id" -o name; then
      adopt "helm_release.$name" "argocd/$id"
    fi
  done
fi

if [ "$ADOPTED" -gt 0 ]; then
  mv "$OUT.tmp" "$OUT"
  echo "Found $ADOPTED leftover resource(s). The plan below adopts them (\"to import\") instead of creating them again."
else
  rm -f "$OUT.tmp" "$OUT"
  echo "Nothing left behind."
fi
