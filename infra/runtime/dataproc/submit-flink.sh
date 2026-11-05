#!/usr/bin/env bash
# Starts the analytics job on Dataproc, or replaces the running one.
# Runs on the Dataproc node through a Pig job's `sh` command, from Terraform or from CI.
#
# Flink runs in per-job mode on YARN, so each job is its own YARN application, named
# warehouse-analytics. Replacing the job means killing that application and submitting
# again. The Kafka consumer group keeps its committed offsets, so the new job resumes
# where the old one stopped.
#
# Usage: submit-flink.sh gs://<bucket>/dataproc/flink.env
# flink.env, written by Terraform, holds JAR_URI, KAFKA_BOOTSTRAP and CHECKPOINT_DIR.
set -euo pipefail

APP_NAME=warehouse-analytics
JAR=/tmp/analytics-flink.jar

gcloud storage cp "$1" /tmp/flink.env
# shellcheck disable=SC1091
. /tmp/flink.env

if ! gcloud storage cp "$JAR_URI" "$JAR"; then
  echo "no Flink JAR at $JAR_URI; run make flink-jar first" >&2
  exit 1
fi

for app in $(yarn application -list -appStates RUNNING,ACCEPTED 2>/dev/null \
  | awk -v name="$APP_NAME" '$2 == name { print $1 }'); do
  yarn application -kill "$app"
done

# The single node offers YARN 2 vcores, and Dataproc's scheduler counts vcores as well
# as memory. Flink would ask one vcore per slot for the TaskManager, which with the
# JobManager's comes to 3, and the TaskManager would never be placed. So it asks for 1.
flink run -d -m yarn-cluster -ynm "$APP_NAME" -yjm 1024m -ytm 2048m -ys 2 \
  -yD yarn.containers.vcores=1 "$JAR" \
  --bootstrap "$KAFKA_BOOTSTRAP" \
  --kafka-auth google \
  --checkpoint-dir "$CHECKPOINT_DIR"
