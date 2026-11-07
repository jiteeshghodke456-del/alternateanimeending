"""Workflow (server-enforced), comments, history, AI usage. BUILD.md P0-7 / P0-9.

States: draft -> checking -> findings_ready -> in_review -> (changes_requested -> draft) | approved | rejected
Rules (return 403 with a human message):
- only the creator can submit, and only when no open BLOCKING finding exists unless overridden
- an override needs a reason; only a reviewer/approver who isn't the creator can make one
- only an approver can approve, and never their own item
Every transition writes an AuditEvent.

Scaffold implemented: comments, history, ai-usage. Workflow transitions are stubs.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException
from sqlmodel import Session, select

from app.deps import CurrentUser, SessionDep
from app.models import AICall, AuditEvent, Comment, ContentItem, User, Version
from app.routers.content import get_item_or_404
from app.schemas import (
    ActionPermission,
    AIUsageGroup,
    AIUsageOut,
    AuditEventOut,
    CommentIn,
    CommentOut,
    FindingOut,
    ItemDetail,
    NoteIn,
    OverrideIn,
)
from app.services import audit, touch

router = APIRouter(tags=["review"])

ACTION_KEYS = ("submit", "approve", "request_changes", "reject", "resubmit", "override", "comment")


# OWNER: workflow-agent
def item_actions(session: Session, item: ContentItem, user: User) -> dict[str, ActionPermission]:
    """What `user` may do with `item` right now, with a human reason when not allowed.
    The UI disables buttons from this (e.g. Kabir approving his own item). STUB: only comment."""
    out = {k: ActionPermission(allowed=False, reason="Not available yet.") for k in ACTION_KEYS}
    out["comment"] = ActionPermission(allowed=True)
    return out


# ------------------------------------------------------------------ transitions (stubs)
# OWNER: workflow-agent
@router.post("/items/{item_id}/submit", response_model=ItemDetail)
def submit_item(item_id: int, session: SessionDep, user: CurrentUser) -> ItemDetail:
    raise HTTPException(status_code=501, detail="Submitting for review isn't available yet.")


# OWNER: workflow-agent
@router.post("/items/{item_id}/approve", response_model=ItemDetail)
def approve_item(item_id: int, session: SessionDep, user: CurrentUser) -> ItemDetail:
    raise HTTPException(status_code=501, detail="Approving isn't available yet.")


# OWNER: workflow-agent
@router.post("/items/{item_id}/request-changes", response_model=ItemDetail)
def request_changes(item_id: int, body: NoteIn, session: SessionDep, user: CurrentUser) -> ItemDetail:
    raise HTTPException(status_code=501, detail="Requesting changes isn't available yet.")


# OWNER: workflow-agent
@router.post("/items/{item_id}/reject", response_model=ItemDetail)
def reject_item(item_id: int, body: NoteIn, session: SessionDep, user: CurrentUser) -> ItemDetail:
    raise HTTPException(status_code=501, detail="Rejecting isn't available yet.")


# OWNER: workflow-agent
@router.post("/findings/{finding_id}/override", response_model=FindingOut)
def override_finding(finding_id: int, body: OverrideIn, session: SessionDep, user: CurrentUser) -> FindingOut:
    raise HTTPException(status_code=501, detail="Overrides aren't available yet.")


# ------------------------------------------------------------------ comments
def _comment_out(c: Comment, users: dict[str, User]) -> CommentOut:
    u = users.get(c.user_id)
    return CommentOut(id=c.id, item_id=c.item_id, user_id=c.user_id, user_name=u.name if u else c.user_id,
                      role=u.role if u else "creator", body=c.body, created_at=c.created_at)


@router.get("/items/{item_id}/comments", response_model=list[CommentOut])
def list_comments(item_id: int, session: SessionDep, user: CurrentUser) -> list[CommentOut]:
    get_item_or_404(session, item_id)
    users = {u.id: u for u in session.exec(select(User)).all()}
    rows = session.exec(select(Comment).where(Comment.item_id == item_id).order_by(Comment.created_at)).all()
    return [_comment_out(c, users) for c in rows]


@router.post("/items/{item_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(item_id: int, body: CommentIn, session: SessionDep, user: CurrentUser) -> CommentOut:
    item = get_item_or_404(session, item_id)
    c = Comment(item_id=item.id, user_id=user.id, body=body.body.strip())
    session.add(c)
    touch(item)
    session.add(item)
    audit(session, "commented", item_id=item.id, user_id=user.id)
    session.commit()
    session.refresh(c)
    return _comment_out(c, {user.id: user})


# ------------------------------------------------------------------ history
@router.get("/items/{item_id}/history", response_model=list[AuditEventOut])
def item_history(item_id: int, session: SessionDep, user: CurrentUser) -> list[AuditEventOut]:
    get_item_or_404(session, item_id)
    users = {u.id: u for u in session.exec(select(User)).all()}
    rows = session.exec(select(AuditEvent).where(AuditEvent.item_id == item_id).order_by(AuditEvent.created_at, AuditEvent.id)).all()
    return [AuditEventOut(id=e.id, item_id=e.item_id, version_id=e.version_id, user_id=e.user_id,
                          user_name=users[e.user_id].name if e.user_id in users else None,
                          action=e.action, detail=dict(e.detail or {}), created_at=e.created_at) for e in rows]


# ------------------------------------------------------------------ AI used panel
@router.get("/items/{item_id}/ai-usage", response_model=AIUsageOut)
def ai_usage(item_id: int, session: SessionDep, user: CurrentUser) -> AIUsageOut:
    item = get_item_or_404(session, item_id)
    versions = session.exec(select(Version).where(Version.item_id == item.id)).all()
    vids = [v.id for v in versions]
    calls = session.exec(select(AICall).where(AICall.version_id.in_(vids))).all() if vids else []
    groups: dict[tuple[str, str, str], list[AICall]] = defaultdict(list)
    for c in calls:
        groups[(c.provider, c.model, c.purpose)].append(c)
    pii = sum(int((v.stats or {}).get("pii_masked", 0)) for v in versions)
    total = round(sum(c.cost_usd for c in calls), 6)
    return AIUsageOut(
        item_id=item.id, confidential=item.confidential, no_external_services=len(calls) == 0,
        external_calls=len(calls), pii_masked=pii, total_cost_usd=total,
        groups=[AIUsageGroup(provider=p, model=m, purpose=pu, count=len(cs), input_tokens=sum(c.input_tokens for c in cs),
                             cost_usd=round(sum(c.cost_usd for c in cs), 6)) for (p, m, pu), cs in sorted(groups.items())],
    )
