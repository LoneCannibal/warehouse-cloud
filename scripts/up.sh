#!/usr/bin/env bash
# Creates everything billed by the hour. Managed Kafka alone takes 20 to 30 minutes,
# so allow about an hour before a demo. If a run is cut off, for example by a lost
# network connection, run it again: it adopts what the first run created and carries on.
set -euo pipefail

cd "$(dirname "$0")/.."
REGION="${REGION:-asia-south1}"

./scripts/preflight.sh

# A private repository needs Argo CD's read-only deploy key. It is kept outside the
# repository, and reaches Terraform only through this environment variable.
KEY_FILE="${DEPLOY_KEY_FILE:-../argocd_deploy_key}"
if [ -z "${TF_VAR_git_ssh_private_key:-}" ] && [ -f "$KEY_FILE" ]; then
  TF_VAR_git_ssh_private_key="$(cat "$KEY_FILE")"
  export TF_VAR_git_ssh_private_key
fi

terraform -chdir=infra/runtime init -backend-config=backend.hcl -input=false
# After an interrupted run, adopt what it left behind instead of failing on it.
./scripts/recover.sh
terraform -chdir=infra/runtime apply
# The import blocks are needed only once; the adopted resources are in the state now.
rm -f infra/runtime/recovered_imports.tf

CLUSTER=$(terraform -chdir=infra/runtime output -raw cluster_name)
ZONE=$(terraform -chdir=infra/runtime output -raw cluster_zone)
gcloud container clusters get-credentials "$CLUSTER" --zone "$ZONE"

echo "Waiting for Argo CD to sync and the load balancer to get an address..."
for _ in $(seq 1 60); do
  ADDRESS=$(kubectl -n warehouse get ingress warehouse \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [ -n "$ADDRESS" ] && break
  sleep 15
done

if [ -z "${ADDRESS:-}" ]; then
  echo "No load balancer yet. Check with: kubectl -n argocd get applications" >&2
  exit 1
fi

# Safety net. The Prometheus Operator checks once, at start-up, which of its resource
# types exist. The CRDs now sync before it, but if it still started first it would never
# create Prometheus, and every dashboard would stay empty. A fresh operator looks again.
# The output is captured rather than piped to grep, which could abort under pipefail.
for _ in $(seq 1 20); do
  PROMETHEUS=$(kubectl -n monitoring get statefulset -l app.kubernetes.io/name=prometheus \
    -o name 2>/dev/null || true)
  [ -n "$PROMETHEUS" ] && break
  sleep 15
done
if [ -z "${PROMETHEUS:-}" ]; then
  echo "Prometheus has not started. Restarting its operator so it looks for its CRDs again."
  kubectl -n monitoring delete pod -l app=kube-prometheus-stack-operator || true
fi

# Grafana's own load balancer usually has its address by now.
for _ in $(seq 1 20); do
  GRAFANA=$(kubectl -n monitoring get service monitoring-grafana \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [ -n "$GRAFANA" ] && break
  sleep 15
done

echo "Public URL: http://$ADDRESS"
echo "The load balancer can take a few more minutes to pass its health checks."
echo "Check it with: BASE_URL=http://$ADDRESS make smoke"
if [ -n "${GRAFANA:-}" ]; then
  echo "Grafana: http://$GRAFANA (user admin; make grafana prints the password)"
else
  echo "Grafana has no address yet. make grafana prints it when it does."
fi
