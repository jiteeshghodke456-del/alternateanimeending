"""Shared FastAPI dependencies: DB session, signed demo session, role guards.

Session token = itsdangerous-signed {"user_id", "role"}. It is accepted from (in order):
1. cookie `bg_session`, 2. `Authorization: Bearer <token>`, 3. `?token=` query (for EventSource/SSE).
The role is re-read from the DB on every request (the cookie role is only a hint).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session
from app.models import User

COOKIE_NAME = "bg_session"
_SALT = "brandgate-session-v1"

SessionDep = Annotated[Session, Depends(get_session)]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SALT)


def make_token(user: User) -> str:
    return _serializer().dumps({"user_id": user.id, "role": user.role})


def read_token(token: str) -> Optional[dict]:
    try:
        data = _serializer().loads(token, max_age=get_settings().session_max_age_s)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


def _token_from_request(request: Request) -> Optional[str]:
    if tok := request.cookies.get(COOKIE_NAME):
        return tok
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.query_params.get("token") or None


def optional_user(request: Request, session: SessionDep) -> Optional[User]:
    token = _token_from_request(request)
    if not token:
        return None
    data = read_token(token)
    if not data:
        return None
    return session.get(User, str(data.get("user_id", "")))


def current_user(user: Annotated[Optional[User], Depends(optional_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in first (pick a demo role on the home page).")
    return user


CurrentUser = Annotated[User, Depends(current_user)]

ROLE_LABELS = {"admin": "Brand Admin", "creator": "Creator", "reviewer": "Reviewer", "approver": "Approver"}


def require_role(*roles: str) -> Callable[[User], User]:
    """Dependency factory: `user: User = Depends(require_role("admin"))`."""

    def _guard(user: CurrentUser) -> User:
        if user.role not in roles:
            names = " or ".join(ROLE_LABELS.get(r, r) for r in roles)
            raise HTTPException(status_code=403, detail=f"Only a {names} can do this. You are signed in as {user.name} ({ROLE_LABELS.get(user.role, user.role)}).")
        return user

    return _guard


def ensure_role(user: User, *roles: str) -> None:
    """Imperative variant of require_role for checks inside a handler."""
    require_role(*roles)(user)


AdminUser = Annotated[User, Depends(require_role("admin"))]
