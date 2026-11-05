#!/usr/bin/env bash
# Prints the web UIs of the Dataproc cluster. The Component Gateway serves them over
# HTTPS behind your Google sign-in, so no tunnel or open port is needed. The running
# job's Flink dashboard is linked from the YARN ResourceManager page, as the
# warehouse-analytics application's "ApplicationMaster" link.
set -euo pipefail

REGION="${REGION:-asia-south1}"
CLUSTER="${CLUSTER:-warehouse-analytics}"

gcloud dataproc clusters describe "$CLUSTER" --region "$REGION" \
  --format='value(config.endpointConfig.httpPorts)' | tr ';' '\n'
