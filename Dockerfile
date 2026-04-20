# syntax=docker/dockerfile:1.7
#
# z3rno-server — FastAPI REST server for Z3rno
# Multi-stage build: builder installs deps, runtime copies only what's needed.

# ---------------------------------------------------------------------------
# Stage 1: builder — install dependencies with uv
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY src/ src/

# Install dependencies into the system site-packages
RUN uv pip install --system -e ".[worker]"

# ---------------------------------------------------------------------------
# Stage 2: runtime — lean image with only installed packages and source
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="z3rno-server" \
      org.opencontainers.image.source="https://github.com/the-ai-project-co/z3rno-server" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system z3rno && useradd --system --gid z3rno --create-home z3rno

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY --chown=z3rno:z3rno src/ src/

USER z3rno

EXPOSE 8000

CMD ["uvicorn", "z3rno_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
