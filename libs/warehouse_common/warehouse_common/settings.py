"""Runtime configuration, read from the environment.

On GKE the values come from the `infra-outputs` ConfigMap that Terraform writes,
so the manifests in gitops/ stay free of environment-specific values.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    service: str
    kafka_bootstrap: str
    kafka_security_protocol: str
    kafka_auth: str
    zk_hosts: str
    zk_session_timeout: float
    db_dsn: str
    db_pool_size: int
    gcp_project: str
    firestore_database: str
    data_bucket: str
    workers_collection: str
    shipments_collection: str
    analytics_collection: str
    inventory_target: str
    workers_target: str
    scheduler_target: str
    scheduler_url: str
    worker_id: str
    worker_slots: int
    http_port: int
    grpc_port: int

    @classmethod
    def from_env(cls, service: str) -> "Settings":
        env = os.environ.get
        return cls(
            service=service,
            kafka_bootstrap=env("KAFKA_BOOTSTRAP", "localhost:9092"),
            kafka_security_protocol=env("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
            # "google" for Managed Service for Apache Kafka, "none" for a local broker.
            kafka_auth=env("KAFKA_AUTH", "none"),
            zk_hosts=env("ZK_HOSTS", "localhost:2181"),
            zk_session_timeout=float(env("ZK_SESSION_TIMEOUT", "10")),
            db_dsn=env("DB_DSN", "postgresql://warehouse:warehouse@localhost:5432/warehouse"),
            db_pool_size=int(env("DB_POOL_SIZE", "3")),
            gcp_project=env("GCP_PROJECT", "local-project"),
            firestore_database=env("FIRESTORE_DATABASE", "(default)"),
            data_bucket=env("DATA_BUCKET", "warehouse-data"),
            workers_collection=env("WORKERS_COLLECTION", "workers"),
            shipments_collection=env("SHIPMENTS_COLLECTION", "shipments"),
            analytics_collection=env("ANALYTICS_COLLECTION", "analytics_zones"),
            inventory_target=env("INVENTORY_TARGET", "localhost:50051"),
            workers_target=env("WORKERS_TARGET", "localhost:50052"),
            scheduler_target=env("SCHEDULER_TARGET", "localhost:50053"),
            scheduler_url=env("SCHEDULER_URL", "http://localhost:8083"),
            worker_id=env("WORKER_ID", socket.gethostname()),
            worker_slots=int(env("WORKER_SLOTS", "2")),
            http_port=int(env("HTTP_PORT", "8080")),
            grpc_port=int(env("GRPC_PORT", "50051")),
        )
