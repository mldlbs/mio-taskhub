import os
import secrets
from fastapi.responses import JSONResponse


def get_token(args_token=None):
    return os.environ.get("MIO_TASKHUB_TOKEN") or args_token or ""


def generate_token() -> str:
    return secrets.token_urlsafe(24)


def make_auth_middleware():
    async def auth_middleware(request, call_next):
        token = getattr(request.app.state, "auth_token", "")
        if not token:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if secrets.compare_digest(auth, f"Bearer {token}"):
            return await call_next(request)
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return auth_middleware
