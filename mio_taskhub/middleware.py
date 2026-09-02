"""Request ID middleware — injects correlation ID into context + response."""
import logging
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

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
