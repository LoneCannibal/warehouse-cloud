#!/usr/bin/env bash
# End-to-end check: posts one order and waits for it to reach DISPATCHED.
# Works against any deployment:
#   BASE_URL=http://localhost:8080 ./scripts/smoke.sh          (Docker Compose)
#   BASE_URL=http://<load-balancer-ip> ./scripts/smoke.sh      (Google Cloud)
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120}"

ORDER=$(curl -fsS -X POST "$BASE_URL/v1/orders" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: smoke-$(date +%s%N)" \
  -d '{"customer":"smoke-test","priority":5,"lines":[{"sku":"SKU-A001","quantity":2},{"sku":"SKU-B002","quantity":1},{"sku":"SKU-C003","quantity":3}]}')
ORDER_ID=$(printf '%s' "$ORDER" | sed -n 's/.*"order_id":"\([^"]*\)".*/\1/p')
echo "accepted $ORDER_ID"

DEADLINE=$(( $(date +%s) + TIMEOUT_SECONDS ))
LAST=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  STATUS=$(curl -fsS "$BASE_URL/v1/orders/$ORDER_ID" | sed -n 's/.*"fulfilment":{"order_id":"[^"]*","status":"\([A-Z]*\)".*/\1/p')
  if [ "$STATUS" != "$LAST" ] && [ -n "$STATUS" ]; then
    echo "status $STATUS"
    LAST="$STATUS"
  fi
  if [ "$STATUS" = "DISPATCHED" ]; then
    echo "PASS: $ORDER_ID dispatched"
    exit 0
  fi
  sleep 1
done

echo "FAIL: $ORDER_ID stuck at '${LAST:-unknown}' after ${TIMEOUT_SECONDS}s" >&2
exit 1
