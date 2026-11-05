"""Prometheus metrics shared by all services.

The Grafana Service RED dashboard reads these. Prometheus attaches the service
and pod labels when it scrapes, so they are not repeated here.
"""
from __future__ import annotations

import time

import grpc
from prometheus_client import Counter, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests handled.", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency.", ["method", "route"], buckets=LATENCY_BUCKETS
)
GRPC_REQUESTS = Counter("grpc_server_requests_total", "gRPC requests handled.", ["method", "code"])
GRPC_LATENCY = Histogram(
    "grpc_server_request_duration_seconds", "gRPC request latency.", ["method"], buckets=LATENCY_BUCKETS
)
EVENTS_PRODUCED = Counter("kafka_events_produced_total", "Kafka events produced.", ["topic"])
EVENTS_CONSUMED = Counter("kafka_events_consumed_total", "Kafka events consumed.", ["topic"])


class GrpcMetricsInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        method = handler_call_details.method.rsplit("/", 1)[-1]
        inner = handler.unary_unary

        def observed(request, context):
            start = time.perf_counter()
            code = "OK"
            try:
                return inner(request, context)
            except Exception:
                code = "INTERNAL"
                raise
            finally:
                GRPC_REQUESTS.labels(method, code).inc()
                GRPC_LATENCY.labels(method).observe(time.perf_counter() - start)

        return grpc.unary_unary_rpc_method_handler(
            observed,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
