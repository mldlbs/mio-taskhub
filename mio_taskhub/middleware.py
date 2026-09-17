"""Middleware: Request ID + Global Rate Limiting + HTTP observability metrics."""
import logging
import os
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mio_taskhub.observability.logging_config import request_id_var

logger = logging.getLogger("mio_taskhub.middleware")

# ---------- HTTP Observability Metrics ----------

_request_count: dict[str, int] = {}
_error_count: dict[str, int] = {}
_total_duration_ms: dict[str, float] = {}
_request_count_total = 0
_active_requests = 0


def get_http_metrics():
    return {
        "request_count": _request_count,
        "error_count": _error_count,
        "total_duration_ms": _total_duration_ms,
        "request_count_total": _request_count_total,
        "active_requests": _active_requests,
    }


def reset_http_metrics():
    _request_count.clear()
    _error_count.clear()
    _total_duration_ms.clear()
    global _request_count_total, _active_requests
    _request_count_total = 0
    _active_requests = 0


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        request.state.request_id = rid
        global _active_requests, _request_count_total
        _active_requests += 1
        _request_count_total += 1
        start = time.monotonic()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            elapsed_ms = (time.monotonic() - start) * 1000
            key = f"{request.method} {request.url.path}"
            _total_duration_ms[key] = _total_duration_ms.get(key, 0) + elapsed_ms
            status = response.status_code
            _request_count[key] = _request_count.get(key, 0) + 1
            status_class = f"{status // 100}xx"
            # Only 4xx/5xx count as errors; 2xx/3xx are successful responses.
            if status >= 400:
                _error_count[status_class] = _error_count.get(status_class, 0) + 1
            if status >= 500:
                logger.error(
                    "request_error",
                    extra={"request_id": rid, "path": request.url.path, "status": response.status_code, "duration_ms": round(elapsed_ms, 1)},
                )
            return response
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            _error_count["5xx"] = _error_count.get("5xx", 0) + 1
            logger.exception("request_exception: %s", e, extra={"request_id": rid, "path": request.url.path, "duration_ms": round(elapsed_ms, 1)})
            raise
        finally:
            _active_requests -= 1
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
