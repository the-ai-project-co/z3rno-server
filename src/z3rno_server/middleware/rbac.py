"""Role-based access control (RBAC) dependency for FastAPI routes.

Usage:
    @router.post("/endpoint")
    async def my_endpoint(
        _rbac: None = require_role("admin", "write"),
    ):
        ...

Roles:
    - admin: full access to all endpoints
    - write: store, recall, forget, sessions
    - read: recall, get, history, audit (read-only)
    - audit: (reserved for future use)

API key auth (role=None) gets full access for backward compatibility.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request


def require_role(*allowed_roles: str):  # type: ignore[no-untyped-def]
    """FastAPI dependency that checks the user's role against allowed roles.

    Args:
        allowed_roles: One or more role strings that are permitted.

    Returns:
        A FastAPI ``Depends`` instance suitable for use as a route parameter.
    """

    def _check(request: Request) -> None:
        role = getattr(request.state, "role", None)
        # API key auth (no role) gets full access for backward compat
        if role is None:
            return
        if role not in allowed_roles:
            raise HTTPException(
                403,
                f"Role '{role}' not permitted. Required: {', '.join(allowed_roles)}",
            )

    return Depends(_check)
