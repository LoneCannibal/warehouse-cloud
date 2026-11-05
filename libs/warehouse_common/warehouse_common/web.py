"""FastAPI application factory with health and metrics routes."""
from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from warehouse_common.logs import configure_logging
from warehouse_common.metrics import HTTP_LATENCY, HTTP_REQUESTS

UNMEASURED_ROUTES = {"/health", "/metrics"}


def create_app(service: str, lifespan=None) -> FastAPI:
    configure_logging(service)
    app = FastAPI(title=service, lifespan=lifespan)

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = route.path if route else "unmatched"
            if path not in UNMEASURED_ROUTES:
                HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
                HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "service": service}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
