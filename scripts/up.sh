#!/usr/bin/env bash
# Creates everything billed by the hour. Managed Kafka alone takes 20 to 30 minutes,
# so allow about an hour before a demo.
set -euo pipefail

cd "$(dirname "$0")/.."
REGION="${REGION:-asia-south1}"

./scripts/preflight.sh

terraform -chdir=infra/runtime init -backend-config=backend.hcl -input=false
terraform -chdir=infra/runtime apply

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

echo "Public URL: http://$ADDRESS"
echo "The load balancer can take a few more minutes to pass its health checks."
echo "Check it with: BASE_URL=http://$ADDRESS make smoke"
