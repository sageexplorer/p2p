"""Logging middleware for P2P workflow observability.

Logs every request/response with workflow_id when available,
so you can trace what happened across a full P2P cycle.
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("p2p.audit")


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Logs method, path, status, duration, and workflow context for every request."""

    async def dispatch(self, request: Request, call_next):
        # Caller can pass X-Request-ID to correlate agent runs
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        logger.info(
            "request_id=%s method=%s path=%s status=%d duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
