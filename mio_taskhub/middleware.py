"""Request ID middleware — injects correlation ID into context + response."""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from mio_taskhub.logging_config import request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
