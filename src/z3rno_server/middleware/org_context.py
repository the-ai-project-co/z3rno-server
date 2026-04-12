"""Org context middleware - sets RLS session variable per request.

At the start of every database transaction, runs:
  SET LOCAL app.current_org_id = '<uuid>'

This is the single enforcement point for multi-tenant isolation.
"""

from __future__ import annotations

# This middleware will be wired as a FastAPI dependency rather than
# a Starlette BaseHTTPMiddleware, because it needs access to the
# database session which is a per-request dependency.
#
# The pattern is:
#   async def get_db_session(request: Request) -> AsyncSession:
#       session = ...
#       org_id = request.state.org_id
#       if org_id:
#           await session.execute(text(f"SET LOCAL app.current_org_id = '{org_id}'"))
#       yield session
#
# See dependencies.py for the implementation.
