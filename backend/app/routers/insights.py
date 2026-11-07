"""Insights dashboard (BUILD.md P0-8). OWNER: workflow-agent.

Every number is computed from the DB on request. Definitions:
- findings: all non-"pass" findings across every version (a re-check of a fixed version adds its own findings).
- fix accept rate: findings with status "accepted" / findings that had a suggested fix.
- avg check time: mean Version.stats.elapsed_ms over finished checks that recorded a time.
- items checked: distinct items with at least one finished check.
- Jev judgments: sum of Version.stats.judgments. Cost: sum of AICall.cost_usd.
- dismissal rate per rule: dismissed / findings for that rule; "possibly unclear" when rate > 0.4 and findings >= 3.
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter
from sqlmodel import select

from app.deps import CurrentUser, SessionDep
from app.models import AICall, ContentItem, Finding, Rule, Version
from app.schemas import CountBucket, DismissalRate, InsightsKpis, InsightsOut

router = APIRouter(tags=["insights"])

UNCLEAR_RATE = 0.40
UNCLEAR_MIN_COUNT = 3


@router.get("/insights", response_model=InsightsOut)
def insights(session: SessionDep, user: CurrentUser) -> InsightsOut:
    versions = session.exec(select(Version)).all()
    done = [v for v in versions if v.check_status == "done"]
    findings = [f for f in session.exec(select(Finding)).all() if f.band != "pass"]
    items = {i.id: i for i in session.exec(select(ContentItem)).all()}
    v_item = {v.id: v.item_id for v in versions}
    cost = sum(c.cost_usd for c in session.exec(select(AICall)).all())
    statements = {}
    for r in session.exec(select(Rule)).all():
        statements.setdefault(r.code, r.statement)

    with_fix = [f for f in findings if f.fix]
    accepted = [f for f in with_fix if f.status == "accepted"]
    elapsed = [int((v.stats or {}).get("elapsed_ms", 0) or 0) for v in done]
    elapsed = [e for e in elapsed if e > 0]

    by_rule = Counter(f.rule_code for f in findings if f.rule_code)
    dismissed = Counter(f.rule_code for f in findings if f.rule_code and f.status == "dismissed")
    rates = sorted(
        (DismissalRate(rule_code=code, findings=n, dismissed=dismissed[code], rate=round(dismissed[code] / n, 3),
                       possibly_unclear=n >= UNCLEAR_MIN_COUNT and dismissed[code] / n > UNCLEAR_RATE,
                       statement=statements.get(code))
         for code, n in by_rule.items()),
        key=lambda d: (-d.rate, -d.findings, d.rule_code))
    by_channel = Counter(items[v_item[f.version_id]].channel for f in findings if v_item.get(f.version_id) in items)

    return InsightsOut(
        kpis=InsightsKpis(
            items_checked=len({v.item_id for v in done}),
            findings=len(findings),
            fix_accept_rate=round(len(accepted) / len(with_fix), 3) if with_fix else 0.0,
            avg_check_ms=int(sum(elapsed) / len(elapsed)) if elapsed else 0,
            jev_judgments=sum(int((v.stats or {}).get("judgments", 0) or 0) for v in versions),
            total_cost_usd=round(cost, 6),
        ),
        by_category=[CountBucket(key=k, count=n) for k, n in Counter(f.category for f in findings).most_common()],
        top_rules=[CountBucket(key=k, count=n, label=statements.get(k)) for k, n in sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))[:10]],
        dismissal_rate=rates,
        by_channel=[CountBucket(key=k, count=n) for k, n in by_channel.most_common()],
    )
