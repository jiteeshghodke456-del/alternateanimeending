"""Demo auth: role picker login, /me, reset. OWNER: backend (scaffold agent)."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import select

from app.config import get_settings
from app.deps import COOKIE_NAME, AdminUser, CurrentUser, SessionDep, current_user, make_token, optional_user, require_role  # noqa: F401
from app.models import User
from app.schemas import LoginIn, LoginOut, MeOut, OkOut, UserOut

router = APIRouter(tags=["auth"])

_ROLE_ORDER = {"admin": 0, "creator": 1, "reviewer": 2, "approver": 3}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=get_settings().session_max_age_s, httponly=True, samesite="lax", path="/"
    )


@router.post("/demo/login", response_model=LoginOut)
def demo_login(body: LoginIn, response: Response, session: SessionDep) -> LoginOut:
    if not get_settings().demo_mode:
        raise HTTPException(status_code=403, detail="Demo sign-in is turned off on this server.")
    user = session.get(User, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="That demo user doesn't exist. Try resetting the demo.")
    token = make_token(user)
    _set_cookie(response, token)
    return LoginOut(user=UserOut.model_validate(user), token=token)


@router.post("/demo/logout", response_model=OkOut)
def demo_logout(response: Response) -> OkOut:
    response.delete_cookie(COOKIE_NAME, path="/")
    return OkOut(message="Signed out.")


@router.get("/me", response_model=MeOut)
def get_me(session: SessionDep, user: Annotated[Optional[User], Depends(optional_user)]) -> MeOut:
    demo = get_settings().demo_mode
    users = sorted(session.exec(select(User)).all(), key=lambda u: (_ROLE_ORDER.get(u.role, 9), u.name)) if demo else []
    return MeOut(
        user=UserOut.model_validate(user) if user else None,
        demo_mode=demo,
        users=[UserOut.model_validate(u) for u in users],
    )


@router.post("/demo/reset", response_model=OkOut)
def demo_reset(user: AdminUser) -> OkOut:
    """Admin only: drop all tables, re-seed, wipe uploads. The caller's session stays valid
    because seeded user ids are stable."""
    if not get_settings().demo_mode:
        raise HTTPException(status_code=403, detail="Reset is only available in demo mode.")
    from app import workspaces as W

    ws = W.current_id()
    if ws is not None:  # workspace mode: only this visitor's workspace goes back to its template
        from app.routers import innovations

        m = W.reset(ws)
        innovations._active.difference_update({k for k in list(innovations._active) if isinstance(k, tuple) and k[0] == ws})
        what = "the demo brand" if m.kind == "demo" else "a blank brand"
        return OkOut(message=f"Workspace reset to {what}. Other visitors' workspaces are unchanged.")
    from app.seed import reset_and_seed

    summary = reset_and_seed()
    from app.pipeline import run as pipeline_run
    from app.routers import innovations

    innovations._active.clear()  # audience runs of the dropped versions
    if pipeline_run.restart_prerun_after_reset():  # re-check the seeded items so the dashboard isn't empty
        summary += "; checking the demo items in the background"
    return OkOut(message=f"Demo reset: {summary}")
