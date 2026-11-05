#!/usr/bin/env bash
# Destroys everything billed by the hour.
#
# Order matters. Kubernetes, not Terraform, created the load balancer and the
# ZooKeeper disks. If Terraform went first, the leftover load balancer would block
# deleting the network, and the disks would keep billing. Deleting the root
# Application removes them, because every Application carries the Argo CD finalizer.
set -euo pipefail

cd "$(dirname "$0")/.."
ZONE="${ZONE:-asia-south1-a}"
CLUSTER="${CLUSTER:-warehouse}"

if gcloud container clusters describe "$CLUSTER" --zone "$ZONE" > /dev/null 2>&1; then
  gcloud container clusters get-credentials "$CLUSTER" --zone "$ZONE"

  if kubectl -n argocd get application root > /dev/null 2>&1; then
    echo "Removing everything Argo CD deployed..."
    kubectl -n argocd delete application root --timeout=15m

    echo "Waiting for the load balancer and the disks to go..."
    for _ in $(seq 1 40); do
      # A failed poll must not stop the script, or everything would be left billing.
      INGRESSES=$(kubectl -n warehouse get ingress -o name 2>/dev/null | wc -l || true)
      VOLUMES=$(kubectl get pv -o name 2>/dev/null | wc -l || true)
      if [ "$INGRESSES" -eq 0 ] && [ "$VOLUMES" -eq 0 ]; then
        break
      fi
      sleep 15
    done
  fi
fi

terraform -chdir=infra/runtime init -backend-config=backend.hcl -input=false
terraform -chdir=infra/runtime destroy

echo "Checking for anything left billing..."
echo "Load balancer forwarding rules:"
gcloud compute forwarding-rules list --format='value(name)' || true
echo "Disks not attached to anything:"
gcloud compute disks list --filter='-users:*' --format='value(name,zone)' || true
