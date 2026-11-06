#!/usr/bin/env bash
# Failure injection for the ZooKeeper demo. Deleting a pod is not a deployment, so
# this does not break the rule that only Argo CD deploys services.
#
#   ./scripts/chaos.sh status          leader, workers and ensemble roles
#   ./scripts/chaos.sh kill-leader     kill the Job Scheduler leader
#   ./scripts/chaos.sh kill-picker     crash one picker, with no graceful shutdown
#   ./scripts/chaos.sh hang-picker     picker stays connected but stops renewing leases
#   ./scripts/chaos.sh resume-picker   let it report late, so fencing rejects the result
#   ./scripts/chaos.sh kill-zookeeper  kill the ZooKeeper ensemble leader
set -euo pipefail

NS=warehouse
EXEC_PY='import sys, urllib.request as u; print(u.urlopen(u.Request(sys.argv[1], method=sys.argv[2])).read().decode())'

http_in_pod() { # pod url method
  kubectl -n "$NS" exec "$1" -- python -c "$EXEC_PY" "$2" "$3"
}

scheduler_leader() {
  for pod in $(kubectl -n "$NS" get pods -l app.kubernetes.io/name=job-scheduler -o name); do
    pod=${pod#pod/}
    if http_in_pod "$pod" http://localhost:8080/v1/leader GET | grep -q '"leading":true'; then
      echo "$pod"
      return
    fi
  done
}

zookeeper_mode() {
  kubectl -n "$NS" exec "$1" -- bash -c 'echo srvr | nc -w 2 localhost 2181 | grep Mode | cut -d" " -f2'
}

zookeeper_leader() {
  for pod in zookeeper-0 zookeeper-1 zookeeper-2; do
    if [ "$(zookeeper_mode "$pod")" = "leader" ]; then
      echo "$pod"
      return
    fi
  done
}

first_picker() {
  # No `| head -1`: under pipefail, kubectl can die of SIGPIPE when head stops
  # reading, and the whole script would abort.
  local pods
  pods=$(kubectl -n "$NS" get pods -l app.kubernetes.io/name=picking -o name)
  pods=${pods%%$'\n'*}
  echo "${pods#pod/}"
}

case "${1:-status}" in
  status)
    echo "Scheduler leader: $(scheduler_leader)"
    for pod in zookeeper-0 zookeeper-1 zookeeper-2; do
      echo "$pod: $(zookeeper_mode "$pod")"
    done
    kubectl -n "$NS" get hpa
    ;;
  kill-leader)
    LEADER=$(scheduler_leader)
    echo "Killing $LEADER"
    kubectl -n "$NS" delete pod "$LEADER" --grace-period=0 --force
    ;;
  kill-picker)
    POD=$(first_picker)
    echo "Killing $POD"
    kubectl -n "$NS" delete pod "$POD" --grace-period=0 --force
    ;;
  hang-picker)
    POD=$(first_picker)
    echo "Hanging $POD"
    http_in_pod "$POD" http://localhost:8080/chaos/hang POST
    ;;
  resume-picker)
    POD=$(first_picker)
    echo "Resuming $POD"
    http_in_pod "$POD" http://localhost:8080/chaos/resume POST
    ;;
  kill-zookeeper)
    LEADER=$(zookeeper_leader)
    echo "Killing $LEADER"
    kubectl -n "$NS" delete pod "$LEADER" --grace-period=0 --force
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 1
    ;;
esac
