# syntax=docker/dockerfile:1.7
#
# z3rno-server — FastAPI REST server for Z3rno
#
# PLACEHOLDER for Phase 1 / Week 1. Builds an image with Python 3.12 and
# uvicorn, sufficient for docker-compose wiring. The real server code lands
# in Week 3 (docs/02-Detailed-Task-Breakdown.md → Week 3 Monday–Friday).
#
# When Week 3 starts, this Dockerfile will:
#   - Install z3rno-core + z3rno-server dependencies via uv sync
#   - Copy src/z3rno_server into the image
#   - Run uvicorn with src/z3rno_server/main.py as the ASGI entrypoint
#
# For now, the command is a tail -f /dev/null keepalive so the compose stack
# stays running while we iterate on the other services.

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="z3rno-server" \
      org.opencontainers.image.description="FastAPI REST server for Z3rno (placeholder scaffold)" \
      org.opencontainers.image.source="https://github.com/the-ai-project-co/z3rno-server" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="The AI Project Co."

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Minimal runtime dependencies — expanded in Week 3 when real code lands.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for the server process.
RUN groupadd --system z3rno && useradd --system --gid z3rno --create-home z3rno

WORKDIR /app

# Placeholder: install uvicorn so the container can boot. Real dependencies
# come from pyproject.toml in Week 3.
RUN pip install --no-cache-dir "uvicorn[standard]>=0.32" "fastapi>=0.115"

# Copy any src/ that exists (harmless if it doesn't yet).
COPY --chown=z3rno:z3rno . /app/

USER z3rno

EXPOSE 8000

# Placeholder health endpoint: the real /v1/health comes in Week 3 Tuesday.
# Until then, this dockerfile is built only so compose can bring up the stack
# and verify wiring; the server container stays in a keepalive loop.
CMD ["sh", "-c", "echo 'z3rno-server placeholder — replace with: uvicorn z3rno_server.main:app --host 0.0.0.0 --port 8000' && tail -f /dev/null"]
