"""gRPC channel and server settings.

gRPC keeps one long-lived HTTP/2 connection, so a plain ClusterIP Service would
pin each client to a single pod and new HPA replicas would get no traffic. Three
settings fix that together: a headless Service exposes every pod IP, round_robin
balances across them, and max_connection_age makes clients re-resolve DNS.

The retry policy covers a replica dying mid-call. Without it, a worker that finished a
task loses the result when its CompleteTask lands on a pod that has just been killed.
UNAVAILABLE is safe to retry here, because every RPC in this system is idempotent or
fenced: ReserveStock is keyed by order, ConfirmPick by task, CompleteTask by attempt.
"""
from __future__ import annotations

import json
from concurrent import futures

import grpc

from warehouse_common.metrics import GrpcMetricsInterceptor

SERVICE_CONFIG = {
    "loadBalancingConfig": [{"round_robin": {}}],
    "methodConfig": [
        {
            "name": [{}],
            "retryPolicy": {
                "maxAttempts": 4,
                "initialBackoff": "0.1s",
                "maxBackoff": "1s",
                "backoffMultiplier": 2,
                "retryableStatusCodes": ["UNAVAILABLE"],
            },
        }
    ],
}
CHANNEL_OPTIONS = [("grpc.service_config", json.dumps(SERVICE_CONFIG))]
SERVER_OPTIONS = [
    ("grpc.max_connection_age_ms", 30_000),
    ("grpc.max_connection_age_grace_ms", 5_000),
]


def channel(target: str) -> grpc.Channel:
    return grpc.insecure_channel(target, options=CHANNEL_OPTIONS)


def serve(port: int, register: list, threads: int = 16) -> grpc.Server:
    """Starts a server. `register` is a list of (add_servicer_fn, servicer) pairs."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=threads),
        interceptors=[GrpcMetricsInterceptor()],
        options=SERVER_OPTIONS,
    )
    for add_servicer, servicer in register:
        add_servicer(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server
