# syntax=docker/dockerfile:1.7
#
# z3rno-server — FastAPI REST server for Z3rno

FROM python:3.12-slim-bookworm AS base

LABEL org.opencontainers.image.title="z3rno-server" \
      org.opencontainers.image.source="https://github.com/the-ai-project-co/z3rno-server" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN groupadd --system z3rno && useradd --system --gid z3rno --create-home z3rno

WORKDIR /app

# Copy project files
COPY --chown=z3rno:z3rno pyproject.toml ./
COPY --chown=z3rno:z3rno src/ src/

# Install dependencies
RUN uv pip install --system -e ".[worker]"

USER z3rno

EXPOSE 8000

CMD ["uvicorn", "z3rno_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
