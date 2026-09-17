# ---------- stage 1: build the React frontend ----------
FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ---------- stage 2: python runtime ----------
FROM python:3.12-slim

# docker CLI only (no daemon): the backend spawns `docker run` for the
# LinkedIn MCP server against the HOST daemon via the mounted socket.
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app/backend

# Dependencies first for layer caching.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# Application code.
COPY backend/app ./app

# Built SPA served by FastAPI (mounted at "/" when this dir exists).
COPY --from=frontend-build /build/dist ./frontend-dist

ENV DATA_DIR=/app/backend/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4)"

# 0.0.0.0 inside the container; the compose file publishes it on
# 127.0.0.1 of the host only.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
