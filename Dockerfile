# ---- Stage 1: Build React SPA ----
FROM node:20-alpine AS frontend
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm install --frozen-lockfile 2>/dev/null || npm install
COPY web/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && \
    rm -rf /var/lib/apt/lists/*

# Python deps (cached layer)
COPY pyproject.toml README.md ./
COPY mio_taskhub/ ./mio_taskhub/
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir .

# Frontend from stage 1
COPY --from=frontend /app/web/dist ./web/dist

# Data directory
RUN mkdir -p /data/taskhub

# Environment
ENV MIO_TASKHUB_DB=/data/taskhub/taskhub.db
ENV PYTHONUNBUFFERED=1

EXPOSE 48620

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:48620/healthz || exit 1

CMD ["python", "-m", "uvicorn", "mio_taskhub.main:app", "--host", "0.0.0.0", "--port", "48620"]
