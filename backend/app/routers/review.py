"""Workflow (server-enforced), comments, history, AI usage. BUILD.md P0-7 / P0-9.

States: draft -> checking -> findings_ready -> in_review -> (changes_requested -> draft) | approved | rejected
Rules (return 403 with a human message):
- only the creator can submit, and only when no open BLOCKING finding exists unless overridden
- an override needs a reason; only a reviewer/approver who isn't the creator can make one
- only an approver can approve, and never their own item
Every transition writes an AuditEvent.

Permission/state rules live in app/workflow.py (shared by handlers and item_actions).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlmodel import Session, select

from app.deps import CurrentUser, SessionDep
from app import workflow as W
from app.models import AICall, AuditEvent, Comment, ContentItem, Finding, Override, User, Version
from app.pipeline.neutral import JUDGE_LABEL, WRITER_LABEL, provider_label
from app.routers.content import get_finding_or_404, get_item_or_404, get_version_or_404
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
    SubmitIn,
)
from app.services import audit, channel_target, version_score, to_finding_out, to_item_summary, to_version_summary, touch, web_source

router = APIRouter(tags=["review"])

ACTION_KEYS = ("submit", "approve", "request_changes", "reject", "resubmit", "override", "comment")


def _perm(denial: Optional[W.Denial]) -> ActionPermission:
    return ActionPermission(allowed=denial is None, reason=denial[1] if denial else None)


def _raise(denial: Optional[W.Denial]) -> None:
    if denial:
        raise HTTPException(status_code=denial[0], detail=denial[1])


def item_actions(session: Session, item: ContentItem, user: User) -> dict[str, ActionPermission]:
    """What `user` may do with `item` right now, with a human reason when not allowed.
    The UI disables buttons from this (e.g. Kabir approving his own item). Same rules as the handlers."""
    return {
        "submit": _perm(W.can_submit(session, item, user)),
        "approve": _perm(W.can_approve(item, user)),
        "request_changes": _perm(W.can_review_decide(item, user, "request changes")),
        "reject": _perm(W.can_review_decide(item, user, "reject content")),
        "resubmit": _perm(W.can_resubmit(item, user)),
        "override": _perm(W.can_override(item, user)),
        "comment": ActionPermission(allowed=True),
    }


def _transition(session: Session, item: ContentItem, user: User, action: str, to: str, extra: Optional[dict] = None) -> ItemDetail:
    """Move the item to `to`, write the AuditEvent {action, from, to, version}, commit, return ItemDetail."""
    v = W.latest(session, item.id)
    frm = item.state
    item.state = to
    touch(item)
    session.add(item)
    audit(session, action, item_id=item.id, version_id=v.id if v else None, user_id=user.id,
          detail={"from": frm, "to": to, "version": v.n if v else None, **(extra or {})})
    session.commit()
    session.refresh(item)
    versions = session.exec(select(Version).where(Version.item_id == item.id).order_by(Version.n)).all()
    return ItemDetail(item=to_item_summary(session, item), versions=[to_version_summary(x, session) for x in versions],
                      actions=item_actions(session, item, user))


def _require_note(body: NoteIn, what: str) -> str:
    note = (body.note or "").strip()
    if not note:
        raise HTTPException(status_code=422, detail=f"Please add a short note so the creator knows why you {what}.")
    return note


# ------------------------------------------------------------------ transitions
@router.post("/items/{item_id}/submit", response_model=ItemDetail)
def submit_item(item_id: int, session: SessionDep, user: CurrentUser, body: Optional[SubmitIn] = None) -> ItemDetail:
    """Below the channel's target Brand Score, submitting needs a short reason (stored in the audit event and
    shown to reviewers). The score never blocks by itself; open blocking findings do (W.can_submit)."""
    item = get_item_or_404(session, item_id)
    _raise(W.can_submit(session, item, user))
    v = W.latest(session, item.id)
    overridden = len(session.exec(select(Finding).where(Finding.version_id == v.id, Finding.status == "overridden")).all())
    extra: dict = {"overridden_findings": overridden}
    sc = version_score(session, v)
    target = channel_target(session, item.brand_id, item.channel)
    if sc is not None:
        extra.update(score=sc.score, target_score=target)
        if sc.score < target:
            reason = ((body.reason if body else None) or "").strip()
            if len(reason) < 3:
                raise HTTPException(status_code=422, detail=(
                    f"This version scores {sc.score}, below the {item.channel} target of {target}. "
                    "Add a short reason for the reviewer to submit it anyway."))
            extra["below_target_reason"] = reason
    return _transition(session, item, user, "submitted", "in_review", extra)


@router.post("/items/{item_id}/approve", response_model=ItemDetail)
def approve_item(item_id: int, session: SessionDep, user: CurrentUser) -> ItemDetail:
    item = get_item_or_404(session, item_id)
    _raise(W.can_approve(item, user))
    return _transition(session, item, user, "approved", "approved")


@router.post("/items/{item_id}/request-changes", response_model=ItemDetail)
def request_changes(item_id: int, body: NoteIn, session: SessionDep, user: CurrentUser) -> ItemDetail:
    item = get_item_or_404(session, item_id)
    _raise(W.can_review_decide(item, user, "request changes"))
    note = _require_note(body, "are asking for changes")
    out = _transition(session, item, user, "changes_requested", "changes_requested", {"note": note})
    session.add(Comment(item_id=item.id, user_id=user.id, body=f"Changes requested: {note}"))
    session.commit()
    return out


@router.post("/items/{item_id}/reject", response_model=ItemDetail)
def reject_item(item_id: int, body: NoteIn, session: SessionDep, user: CurrentUser) -> ItemDetail:
    item = get_item_or_404(session, item_id)
    _raise(W.can_review_decide(item, user, "reject content"))
    note = _require_note(body, "are rejecting it")
    out = _transition(session, item, user, "rejected", "rejected", {"note": note})
    session.add(Comment(item_id=item.id, user_id=user.id, body=f"Rejected: {note}"))
    session.commit()
    return out


@router.post("/findings/{finding_id}/override", response_model=FindingOut)
def override_finding(finding_id: int, body: OverrideIn, session: SessionDep, user: CurrentUser) -> FindingOut:
    f = get_finding_or_404(session, finding_id)
    v = get_version_or_404(session, f.version_id)
    item = get_item_or_404(session, v.item_id)
    _raise(W.can_override(item, user))
    reason = (body.reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail="Please give a reason of at least 10 characters for the override.")
    if f.severity != "blocking":
        raise HTTPException(status_code=409, detail="Only blocking findings need an override. The creator can simply dismiss this one.")
    if f.status != "open":
        raise HTTPException(status_code=409, detail=f"This finding is already {f.status}; it doesn't need an override.")
    latest = W.latest(session, item.id)
    if latest and latest.id != v.id:
        raise HTTPException(status_code=409, detail="This finding belongs to an older version.")
    f.status = "overridden"
    session.add(f)
    session.add(Override(finding_id=f.id, user_id=user.id, reason=reason))
    from app import precedents  # precedent memory: this decision teaches the next check

    precedents.record(session, f, item, user, "overridden", "override", reason)
    touch(item)
    session.add(item)
    audit(session, "override", item_id=item.id, version_id=v.id, user_id=user.id,
          detail={"from": item.state, "to": item.state, "version": v.n, "finding_id": f.id, "rule_code": f.rule_code, "reason": reason})
    session.commit()
    session.refresh(f)
    return to_finding_out(session, f, item.brand_id)


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
    rows = session.exec(select(AuditEvent).where(AuditEvent.item_id == item_id).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())).all()
    return [AuditEventOut(id=e.id, item_id=e.item_id, version_id=e.version_id, user_id=e.user_id,
                          user_name=users[e.user_id].name if e.user_id in users else None,
                          action=e.action, detail=dict(e.detail or {}), created_at=e.created_at) for e in rows]


# ------------------------------------------------------------------ AI used panel
def _usd(x: float) -> str:
    return f"${x:.4f}" if x < 0.01 else f"${x:.2f}"


@router.get("/items/{item_id}/ai-usage", response_model=AIUsageOut)
def ai_usage(item_id: int, session: SessionDep, user: CurrentUser) -> AIUsageOut:
    item = get_item_or_404(session, item_id)
    versions = session.exec(select(Version).where(Version.item_id == item.id)).all()
    vids = [v.id for v in versions]
    calls = session.exec(select(AICall).where(AICall.version_id.in_(vids))).all() if vids else []
    # The UI never names the models or vendors (neutral.py): providers become neutral labels, models are left out.
    groups: dict[tuple[str, str], list[AICall]] = defaultdict(list)
    for c in calls:
        groups[(provider_label(c.provider), c.purpose)].append(c)
    pii = sum(int((v.stats or {}).get("pii_masked", 0) or 0) for v in versions)
    total = round(sum(c.cost_usd for c in calls), 6)
    if not calls:
        summary = ("No external services used. This item is confidential, so only local code checks ran."
                   if item.confidential else "No external services used so far. Only local code checks have run on this item.")
    else:
        by_provider: dict[str, int] = defaultdict(int)
        for c in calls:
            by_provider[provider_label(c.provider)] += 1
        kinds = [{JUDGE_LABEL: "a judgment model", WRITER_LABEL: "a writing model"}.get(p, "another AI service")
                 for p in sorted(by_provider)]
        n_services = len(by_provider)
        services = (f"{n_services} external AI service{'s' if n_services != 1 else ''} used: "
                    + (" and ".join(kinds) if len(kinds) <= 2 else ", ".join(kinds[:-1]) + " and " + kinds[-1]) + ".")
        parts = ", ".join(f"{n} to the {p}" for p, n in sorted(by_provider.items()))
        summary = f"{services} {len(calls)} call{'s' if len(calls) != 1 else ''} ({parts}), costing {_usd(total)}."
        summary += f" Personal data masked: {pii}." if pii else " No personal data needed masking."
        if item.confidential:
            summary += " Warning: this item is marked confidential but external calls were recorded."
    fetches = [f"Fetched {src.final_url} (no third-party service)" for src in (web_source(v) for v in versions) if src]
    return AIUsageOut(
        fetches=fetches,
        item_id=item.id, confidential=item.confidential, no_external_services=len(calls) == 0,
        external_calls=len(calls), pii_masked=pii, total_cost_usd=total, summary=summary,
        groups=[AIUsageGroup(provider=p, purpose=pu, count=len(cs),
                             input_tokens=sum(c.input_tokens for c in cs), cost_usd=round(sum(c.cost_usd for c in cs), 6))
                for (p, pu), cs in sorted(groups.items())],
    )
