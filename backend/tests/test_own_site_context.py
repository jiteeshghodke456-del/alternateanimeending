"""Regression (real-brand test, 8 Nov): IN-CCPA-05 ("don't make an ad look like a news story, an independent
review or a customer's own post") flagged 11 paragraphs of a brand's OWN dated press note on its own blog at
p 0.72-0.86. Code now knows where a web page is published: on the brand's own site the rule is read only when
Jev says the text poses as an outsider, and post-level rules give one finding per post, not one per paragraph."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app import packs as P
from app.models import Rule
from app.pipeline import engine as E
from app.pipeline import questions as Q
from app.pipeline.context import CheckContext, RuleSpec, UnitIn
from app.pipeline.jev import CallLog
from app.pipeline.webpage import brand_owns_host, page_host
from fakes import FakeJev, install

PACK_FILE = Path(__file__).resolve().parents[1] / "app" / "seed" / "packs" / "india_regulatory.json"
PRESS_NOTE = [
    "[fixture text replaced]",
    "[fixture text replaced]",
    "[fixture text replaced]",
]


def ccpa05() -> RuleSpec:
    pack = P.load_pack_file(PACK_FILE)
    r = next(x for x in pack.rules if x.code == "IN-CCPA-05")
    f = P._fields(pack, r, [])
    return RuleSpec(code=r.code, category=f["category"], severity=f["severity"], statement=f["statement"],
                    check_method=f["check_method"], params=f["params"], examples_violating=f["examples_violating"],
                    examples_ok=f["examples_ok"])


def units(paras: list[str]) -> list[UnitIn]:
    out, pos = [], 0
    for i, t in enumerate(paras):
        out.append(UnitIn(id=i + 1, kind="text", idx=i, text=t, char_start=pos))
        pos += len(t) + 2
    return out


def run(monkeypatch, jev: FakeJev, *, host: str | None, own: bool | None, paras: list[str] = PRESS_NOTE):  # noqa: ANN001, ANN201
    install(monkeypatch, jev)
    ctx = CheckContext(channel="website", rules=[ccpa05()], version_kind="web" if host else "text",
                       post_text="\n\n".join(paras), brand_name="Swiggy", source_host=host, own_site=own)
    return asyncio.run(E.analyze(units(paras), ctx, calls=CallLog(), use_llm=False))


def test_brand_owns_host() -> None:
    assert brand_owns_host("Swiggy", "blog.swiggy.com") and brand_owns_host("Swiggy", "www.swiggy.co.in")
    assert brand_owns_host("Saffron & Steam Coffee Co. (fictional demo brand)", "saffronsteam.in")
    assert not brand_owns_host("Swiggy", "swiggy.example.com")  # a subdomain on someone else's site
    assert not brand_owns_host("Swiggy", "swiggyoffers.example") and not brand_owns_host("Swiggy", "news.example.com")
    assert brand_owns_host("Acme", "shop.acme-store.in", domains=["acme-store.in"])
    assert page_host({"url": "https://x.example/a", "final_url": "https://Blog.Swiggy.com/p/"}) == "blog.swiggy.com"
    assert page_host({}) is None


def test_press_note_on_the_brands_own_site_is_not_a_disguised_ad(monkeypatch) -> None:  # noqa: ANN001
    jev = FakeJev(rules={"IN-CCPA-05": 0.8, Q.OUTSIDE_VOICE: 0.1})
    res = run(monkeypatch, jev, host="blog.swiggy.com", own=True)
    assert [d for d in res.findings if d.rule_code == "IN-CCPA-05"] == []
    asked = [q for c in jev.calls for q in c["questions"]]
    assert asked.count(Q.OUTSIDE_VOICE) == len(PRESS_NOTE)
    assert all(j.p == 0.0 for j in res.judgments if j.rule_code == "IN-CCPA-05")


def test_fake_customer_review_on_the_brands_own_site_is_still_flagged_once(monkeypatch) -> None:  # noqa: ANN001
    jev = FakeJev(rules={"IN-CCPA-05": 0.85, Q.OUTSIDE_VOICE: 0.9})
    res = run(monkeypatch, jev, host="blog.swiggy.com", own=True)
    [d] = [d for d in res.findings if d.rule_code == "IN-CCPA-05"]
    assert d.band == "flag" and d.unit_idx == 0 and "3 paragraphs" in d.problem


def test_unsure_outside_voice_on_own_site_is_at_most_a_human_look_so_flag_only_passes(monkeypatch) -> None:  # noqa: ANN001
    res = run(monkeypatch, FakeJev(rules={"IN-CCPA-05": 0.9, Q.OUTSIDE_VOICE: 0.5}), host="blog.swiggy.com", own=True)
    assert [d for d in res.findings if d.rule_code == "IN-CCPA-05"] == []


def test_off_the_brands_site_nothing_changes_but_one_finding_per_post(monkeypatch) -> None:  # noqa: ANN001
    p = {PRESS_NOTE[0]: 0.75, PRESS_NOTE[1]: 0.86, PRESS_NOTE[2]: 0.72}
    jev = FakeJev(rules={"IN-CCPA-05": lambda t: next((v for k, v in p.items() if k in t), 0.05)})
    for host, own in (("news.example.com", False), (None, None)):
        jev.calls.clear()
        res = run(monkeypatch, jev, host=host, own=own)
        [d] = [d for d in res.findings if d.rule_code == "IN-CCPA-05"]
        assert d.unit_idx == 1 and d.probability == 0.86  # the clearest paragraph
        assert all(Q.OUTSIDE_VOICE not in c["questions"] for c in jev.calls)
        assert len([j for j in res.judgments if j.rule_code == "IN-CCPA-05"]) == 3  # every paragraph still judged


def test_stored_pack_rule_gets_the_current_pack_behaviour() -> None:
    old = Rule(brand_id=1, code="IN-CCPA-05", statement="x", check_method="jev", category="legal", pack_id="india_regulatory",
               params={"flag_only": True, "pack": "india_regulatory", "pack_version": 1, "citation": {"clause": "c"}})
    params = P.current_params(old)
    assert params["post_level"] is True and params["needs_outside_voice"] is True and params["citation"] == {"clause": "c"}
    own = Rule(brand_id=1, code="R-X-01", statement="x", params={"flag_only": False})
    assert P.current_params(own) == {"flag_only": False}


def test_pack_marks_the_post_level_and_own_site_rules() -> None:
    rules = {r["code"]: r["params"] for r in json.loads(PACK_FILE.read_text())["rules"]}
    assert rules["IN-CCPA-05"].get("needs_outside_voice") and rules["IN-CCPA-05"].get("post_level")
    assert rules["IN-ASCI-01"].get("post_level") and rules["IN-ASCI-03"].get("post_level")
    assert not any(v.get("needs_outside_voice") for k, v in rules.items() if k != "IN-CCPA-05")


def test_load_context_reads_the_web_host_and_decides_own_site(client, tmp_path) -> None:  # noqa: ANN001
    from sqlmodel import Session, select

    from app.db import get_engine
    from app.models import Brand, ContentItem, Version
    from app.pipeline.run import load_context

    with Session(get_engine()) as s:
        brand = s.exec(select(Brand)).first()
        item = ContentItem(brand_id=brand.id, title="t", channel="website", creator_id=1, state="draft")
        for url, own in (("https://blog.saffronsteam.in/news/", True), ("https://news.example.com/a", False)):
            rec = tmp_path / f"{own}.json"
            rec.write_text(json.dumps({"url": url, "final_url": url, "a11y": []}))
            ctx, _ = load_context(s, item, Version(id=None, item_id=0, n=1, kind="web", text="", file_path=str(rec)))
            assert ctx.brand_name == brand.name and ctx.source_host == url.split("/")[2] and ctx.own_site is own
        ctx, _ = load_context(s, item, Version(id=None, item_id=0, n=1, kind="text", text="Hi"))
        assert ctx.source_host is None and ctx.own_site is None
