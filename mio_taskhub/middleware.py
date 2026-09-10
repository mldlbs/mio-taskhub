"""Middleware: Request ID + Global Rate Limiting."""
import logging
import os
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mio_taskhub.logging_config import request_id_var

logger = logging.getLogger("mio_taskhub.middleware")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            if response.status_code >= 500:
                logger.error(
                    "request_error",
                    extra={"request_id": rid, "path": request.url.path, "status": response.status_code},
                )
            return response
        except Exception as e:
            logger.exception("request_exception: %s", e, extra={"request_id": rid, "path": request.url.path})
            raise
        finally:
            request_id_var.reset(token)


# ---------- Global Rate Limiter ----------

_rate_buckets: dict[str, list[float]] = {}
_DEFAULT_RATE_LIMIT = 120  # req/min per client IP (global)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP for all /api/ endpoints.

    Configure via MIO_TASKHUB_RATE_LIMIT env var (default 120 req/min).
    Health/metrics/ws endpoints are exempt.
    """

    def __init__(self, app, rate_limit: int | None = None):
        super().__init__(app)
        self.rate_limit = rate_limit or int(os.environ.get("MIO_TASKHUB_RATE_LIMIT", _DEFAULT_RATE_LIMIT))

    async def dispatch(self, request: Request, call_next):
        # Only apply to /api/ paths
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        # Skip rate limiting for health/metrics endpoints
        path = request.url.path
        if any(x in path for x in ("/healthz", "/readyz", "/metrics")):
            return await call_next(request)

        key = f"{client_ip}"
        now = time.time()
        bucket = _rate_buckets.setdefault(key, [])
        # Sliding window: keep only last 60s
        cutoff = now - 60
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= self.rate_limit:
            retry_after = int(bucket[0] - cutoff) + 1
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        return await call_next(request)
