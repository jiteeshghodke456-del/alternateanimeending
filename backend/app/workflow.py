"""Pure workflow helpers (no FastAPI): state labels, permission rules, resubmit text and diff.

Routers turn the `(status_code, message)` tuples returned here into HTTPException, and
`item_actions()` turns the same tuples into the disabled-button reasons the UI shows.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, func, select

from app.models import ContentItem, Finding, Unit, User, Version

Denial = tuple[int, str]  # (http status, human message)

STATE_LABELS = {
    "draft": "a draft", "checking": "being checked", "findings_ready": "ready for submission",
    "in_review": "in review", "changes_requested": "waiting for changes",
    "approved": "approved", "rejected": "rejected",
}
FINAL_STATES = ("approved", "rejected")
SELF_APPROVE_MSG = "You can't approve your own content."


def state_label(state: str) -> str:
    return STATE_LABELS.get(state, state)


def is_creator(user: User, item: ContentItem) -> bool:
    return user.id == item.creator_id


def latest(session: Session, item_id: int) -> Optional[Version]:
    return session.exec(select(Version).where(Version.item_id == item_id).order_by(Version.n.desc())).first()


def open_blocking_count(session: Session, version_id: int) -> int:
    """Blocking findings that are still `open` (accepted/edited/dismissed/overridden all count as handled)."""
    q = select(func.count()).select_from(Finding).where(
        Finding.version_id == version_id, Finding.severity == "blocking",
        Finding.band != "pass", Finding.status == "open")
    return int(session.exec(q).one())


# ------------------------------------------------------------------ permission rules
def can_act_on_finding(user: User, item: ContentItem) -> Optional[Denial]:
    if not (is_creator(user, item) or user.role in ("reviewer", "approver")):
        return 403, "Only the person who created this content, or a reviewer, can act on its findings."
    if item.state in FINAL_STATES:
        return 409, f"This item is already {item.state}, so its findings can no longer be changed."
    return None


def can_submit(session: Session, item: ContentItem, user: User) -> Optional[Denial]:
    if not is_creator(user, item):
        return 403, "Only the person who created this content can submit it for review."
    if item.state == "checking":
        return 409, "The check is still running. Wait for it to finish, then submit."
    if item.state == "in_review":
        return 409, "This item is already in review."
    if item.state in FINAL_STATES:
        return 409, f"This item is already {item.state}."
    if item.state == "changes_requested":
        return 409, "A reviewer asked for changes. Resubmit a new version, wait for the check, then submit."
    v = latest(session, item.id)
    if v is None or v.check_status != "done":
        return 409, "This version hasn't been checked yet. Run the check first, then submit."
    n = open_blocking_count(session, v.id)
    if n:
        return 409, (f"{n} blocking issue{'s are' if n != 1 else ' is'} still open. Fix, edit or dismiss "
                     f"{'them' if n != 1 else 'it'}, or ask a reviewer for an override, before submitting.")
    return None


def can_override(item: ContentItem, user: User) -> Optional[Denial]:
    if user.role not in ("reviewer", "approver"):
        return 403, "Only a Reviewer or Approver can override a blocking finding."
    if is_creator(user, item):
        return 403, "You can't override findings on your own content."
    if item.state in FINAL_STATES:
        return 409, f"This item is already {item.state}."
    return None


def can_approve(item: ContentItem, user: User) -> Optional[Denial]:
    if user.role != "approver":
        return 403, "Only an Approver can approve content."
    if is_creator(user, item):
        return 403, SELF_APPROVE_MSG
    if item.state != "in_review":
        return 409, f"Only content that is in review can be approved. This item is {state_label(item.state)}."
    return None


def can_review_decide(item: ContentItem, user: User, verb: str) -> Optional[Denial]:
    """request-changes / reject."""
    if user.role not in ("reviewer", "approver"):
        return 403, f"Only a Reviewer or Approver can {verb}."
    if is_creator(user, item):
        return 403, f"You can't {verb} on your own content."
    if item.state != "in_review":
        return 409, f"You can only {verb} while content is in review. This item is {state_label(item.state)}."
    return None


def can_resubmit(item: ContentItem, user: User) -> Optional[Denial]:
    if not is_creator(user, item):
        return 403, "Only the person who created this content can resubmit it."
    if item.state == "checking":
        return 409, "The check is still running. Wait for it to finish before resubmitting."
    if item.state == "in_review":
        return 409, "This item is in review. A reviewer has to request changes before you can resubmit."
    if item.state in FINAL_STATES:
        return 409, f"This item is already {item.state}."
    return None


# ------------------------------------------------------------------ resubmit: build the new text
@dataclass
class ApplyResult:
    text: str
    applied: list[int] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {finding_id, reason}


def apply_findings(text: str, findings: list[Finding], units: Optional[dict[int, Unit]] = None) -> ApplyResult:
    """Apply accepted fixes (`fix`) and creator edits (`edited_text`) to `text`.

    Spans are validated and overlaps resolved first (earlier span wins, the later overlapping
    one is skipped and reported), then replacements run from the END of the text to the START
    so earlier offsets stay valid.
    """
    cand: list[tuple[int, int, str, Finding]] = []
    skipped: list[dict] = []
    for f in findings:
        if f.status == "accepted":
            repl = f.fix
        elif f.status == "edited":
            repl = f.edited_text
        else:
            continue
        if repl is None:
            skipped.append({"finding_id": f.id, "reason": "No replacement text was available."})
            continue
        u = units.get(f.unit_id) if units and f.unit_id else None
        if u is not None and u.kind in ("image", "alt_text"):
            skipped.append({"finding_id": f.id, "reason": "This finding is about the image or alt text, not the written text."})
            continue
        s, e = f.span_start, f.span_end
        if s is None or e is None or s < 0 or e < s or e > len(text):
            skipped.append({"finding_id": f.id, "reason": "Its position in the text is unknown or out of range."})
            continue
        cand.append((s, e, repl, f))

    cand.sort(key=lambda c: (c[0], c[1], c[3].id or 0))
    kept: list[tuple[int, int, str, Finding]] = []
    last_end = -1
    for c in cand:
        if kept and c[0] < last_end:
            skipped.append({"finding_id": c[3].id, "reason": f"Overlaps finding {kept[-1][3].id}, so it was skipped. Check it again on the new version."})
            continue
        kept.append(c)
        last_end = c[1]

    out = text
    for s, e, repl, _f in reversed(kept):
        out = out[:s] + repl + out[e:]
    return ApplyResult(text=out, applied=[c[3].id for c in kept], skipped=skipped)


_TOKEN = re.compile(r"\s+|[^\s]+")


def word_diff(old: str, new: str) -> list[dict]:
    """Word-level diff as [{op: equal|insert|delete, text}], whitespace kept so segments concatenate cleanly."""
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append({"op": "equal", "text": "".join(a[i1:i2])})
            continue
        if i2 > i1:
            out.append({"op": "delete", "text": "".join(a[i1:i2])})
        if j2 > j1:
            out.append({"op": "insert", "text": "".join(b[j1:j2])})
    return out
