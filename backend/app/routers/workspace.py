"""Per-visitor workspaces (app/workspaces.py). No sign-in needed.

POST /workspaces {kind: "demo"|"blank"}  -> 201 WorkspaceOut; sets the `bg_ws` cookie and signs out
GET  /workspace                          -> WorkspaceOut with expires_at and limits
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from sqlmodel import Session

from app import workspaces as W
from app.config import get_settings
from app.deps import COOKIE_NAME, SessionDep
from app.schemas import WorkspaceBrand, WorkspaceCreate, WorkspaceLimits, WorkspaceOut
from app.services import NoBrandError, current_brand

router = APIRouter(tags=["workspaces"])

OFF_MESSAGE = "Workspaces are switched off on this server (WORKSPACES_ENABLED=0): everyone shares one demo."


def _brand_name(session: Session) -> str:
    try:
        return current_brand(session).name
    except NoBrandError:
        return ""


def _out(m: W.Meta, brand_name: str, *, full: bool) -> WorkspaceOut:
    s = get_settings()
    return WorkspaceOut(
        id=m.id, kind=m.kind, brand=WorkspaceBrand(name=brand_name), created_at=m.created_iso(),
        expires_at=m.expires_iso() if full else None,
        limits=WorkspaceLimits(max_workspaces=W.max_workspaces(), ttl_hours=s.workspace_ttl_h) if full else None,
    )


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceCreate, response: Response) -> WorkspaceOut:
    ref = W.current_ref()
    if not W.enabled() or ref is None:
        raise HTTPException(status_code=409, detail=OFF_MESSAGE)
    try:
        m = W.create(body.kind)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Couldn't create a workspace right now. Try again in a moment.") from exc
    ref.id, ref.created = m.id, True  # the middleware sets the bg_ws cookie on this response
    response.delete_cookie(COOKIE_NAME, path="/")  # the old sign-in belongs to the old workspace
    with W.use(m.id):
        from app.db import get_engine

        with Session(get_engine()) as s:
            name = _brand_name(s)
    return _out(m, name, full=False)


@router.get("/workspace", response_model=WorkspaceOut)
def get_workspace(session: SessionDep) -> WorkspaceOut:
    ws = W.current_id()
    m = W.get_meta(ws) if ws else None
    if m is None:
        raise HTTPException(status_code=409, detail=OFF_MESSAGE)
    return _out(m, _brand_name(session), full=True)
