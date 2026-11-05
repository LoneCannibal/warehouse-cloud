"""Runs real gRPC servers, because this behaviour lives in the channel configuration."""
from __future__ import annotations

import socket

from warehouse_common import grpc_util
from warehouse_common.rpc import workers_pb2, workers_pb2_grpc


class Replica(workers_pb2_grpc.WorkerRegistryServicer):
    def __init__(self, name: str) -> None:
        self.name = name

    def ListAvailable(self, request, context):
        return workers_pb2.ListAvailableResponse(workers=[workers_pb2.Worker(worker_id=self.name)])


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_replica(name: str, port: int):
    return grpc_util.serve(port, [(workers_pb2_grpc.add_WorkerRegistryServicer_to_server, Replica(name))], threads=2)


def call(stub) -> str:
    return stub.ListAvailable(workers_pb2.ListAvailableRequest(), timeout=5).workers[0].worker_id


def two_replicas():
    ports = free_port(), free_port()
    servers = start_replica("a", ports[0]), start_replica("b", ports[1])
    channel = grpc_util.channel(f"ipv4:127.0.0.1:{ports[0]},127.0.0.1:{ports[1]}")
    channel.subscribe(lambda _state: None, try_to_connect=True)
    return servers, workers_pb2_grpc.WorkerRegistryStub(channel)


def test_round_robin_reaches_every_replica():
    servers, stub = two_replicas()
    try:
        assert {call(stub) for _ in range(20)} == {"a", "b"}
    finally:
        for server in servers:
            server.stop(0)


def test_calls_survive_a_replica_dying():
    # This is the Job Scheduler failover: workers must not lose finished tasks when the
    # replica they were talking to is killed.
    servers, stub = two_replicas()
    try:
        assert {call(stub) for _ in range(20)} == {"a", "b"}

        servers[0].stop(0).wait()

        assert {call(stub) for _ in range(50)} == {"b"}
    finally:
        servers[1].stop(0)
