#!/usr/bin/env bash
# Checks that the project can run the stack before anything is created.
#
# Creation fails partway through if a quota runs short, leaving half a stack billing.
# This compares the free vCPUs with what infra/runtime plans to run, at three levels:
# the region, the whole project, and the machine family. Family quotas can be zero on
# their own: this project has no E2 quota in asia-south1, which is why it uses N2.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-south1}"
FAMILY="${MACHINE_FAMILY:-N2}"
NEEDED="${NEEDED_VCPUS:-8}"

if [ -z "$PROJECT_ID" ]; then
  echo "No project set. Run: gcloud config set project <project-id>" >&2
  exit 1
fi

free_cpus() { # quota JSON on stdin, metric name as $1
  python3 -c '
import json, sys
quotas = json.load(sys.stdin).get("quotas", [])
match = [q for q in quotas if q["metric"] == sys.argv[1]]
print(int(match[0]["limit"] - match[0]["usage"]) if match else 10**6)
' "$1"
}

REGION_JSON=$(gcloud compute regions describe "$REGION" --project "$PROJECT_ID" --format=json)
REGIONAL=$(free_cpus CPUS <<< "$REGION_JSON")
IN_FAMILY=$(free_cpus "${FAMILY}_CPUS" <<< "$REGION_JSON")
GLOBAL=$(gcloud compute project-info describe --project "$PROJECT_ID" --format=json | free_cpus CPUS_ALL_REGIONS)

echo "vCPUs free: $REGIONAL in $REGION, $IN_FAMILY of $FAMILY there, $GLOBAL across the project. The stack needs $NEEDED."
for free in "$REGIONAL" "$IN_FAMILY" "$GLOBAL"; do
  if [ "$free" -lt "$NEEDED" ]; then
    echo "Not enough vCPUs. Stop other VMs, use fewer GKE nodes (gke_node_count), or pick a machine family with quota." >&2
    exit 1
  fi
done
