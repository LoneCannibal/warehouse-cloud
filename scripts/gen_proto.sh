#!/usr/bin/env bash
# Regenerates the Python gRPC stubs in warehouse_common/rpc from proto/.
# protoc derives Python imports from the proto path, so the files are copied
# into a matching tree first. That keeps proto/ free of Python package names.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/warehouse_common/rpc"
cp "$ROOT"/proto/*.proto "$WORK/warehouse_common/rpc/"

"$PYTHON" -m grpc_tools.protoc \
  -I "$WORK" \
  --python_out="$ROOT/libs/warehouse_common" \
  --grpc_python_out="$ROOT/libs/warehouse_common" \
  "$WORK"/warehouse_common/rpc/*.proto
