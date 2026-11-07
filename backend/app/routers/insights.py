"""Insights dashboard (BUILD.md P0-8). OWNER: workflow-agent.

Scaffold returns real counts from the DB with simple queries so the dashboard can be built;
refine definitions (e.g. which findings count, fix-accept rate denominator) as needed.
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter
from sqlmodel import select

from app.deps import CurrentUser, SessionDep
from app.models import AICall, ContentItem, Finding, Version
from app.schemas import CountBucket, DismissalRate, InsightsKpis, InsightsOut

router = APIRouter(tags=["insights"])


@router.get("/insights", response_model=InsightsOut)
def insights(session: SessionDep, user: CurrentUser) -> InsightsOut:
    versions = session.exec(select(Version)).all()
    done = [v for v in versions if v.check_status == "done"]
    findings = [f for f in session.exec(select(Finding)).all() if f.band != "pass"]
    items = {i.id: i for i in session.exec(select(ContentItem)).all()}
    v_item = {v.id: v.item_id for v in versions}
    jev_calls = session.exec(select(AICall)).all()

    with_fix = [f for f in findings if f.fix]
    accepted = [f for f in with_fix if f.status == "accepted"]
    elapsed = [int((v.stats or {}).get("elapsed_ms", 0)) for v in done]

    by_rule = Counter(f.rule_code for f in findings if f.rule_code)
    dismissed = Counter(f.rule_code for f in findings if f.rule_code and f.status == "dismissed")
    rates = [
        DismissalRate(rule_code=code, findings=n, dismissed=dismissed[code], rate=round(dismissed[code] / n, 3),
                      possibly_unclear=dismissed[code] / n > 0.40)
        for code, n in by_rule.most_common()
    ]
    by_channel = Counter(items[v_item[f.version_id]].channel for f in findings if v_item.get(f.version_id) in items)

    return InsightsOut(
        kpis=InsightsKpis(
            items_checked=len({v.item_id for v in done}),
            findings=len(findings),
            fix_accept_rate=round(len(accepted) / len(with_fix), 3) if with_fix else 0.0,
            avg_check_ms=int(sum(elapsed) / len(elapsed)) if elapsed else 0,
            jev_judgments=sum(int((v.stats or {}).get("judgments", 0)) for v in versions),
            total_cost_usd=round(sum(c.cost_usd for c in jev_calls), 6),
        ),
        by_category=[CountBucket(key=k, count=n) for k, n in Counter(f.category for f in findings).most_common()],
        top_rules=[CountBucket(key=k, count=n) for k, n in by_rule.most_common(10)],
        dismissal_rate=rates,
        by_channel=[CountBucket(key=k, count=n) for k, n in by_channel.most_common()],
    )
