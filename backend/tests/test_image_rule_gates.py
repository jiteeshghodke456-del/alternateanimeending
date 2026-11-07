"""Regression (real-brand test, 8 Nov): logo and type rules imported from a guideline PDF were asked of every
photo on a web page; print specs (kerning, leading, pt sizes) were judged from pixels."""
from app.pipeline import questions as Q
from app.pipeline.context import RuleSpec


def _r(statement: str, params=None) -> RuleSpec:
    return RuleSpec(code="R-X", category="visual", severity="major", statement=statement, check_method="visual",
                    params=params if params is not None else {"imported_from": "abc"})


def test_logo_rules_need_a_brand_mark_in_the_image():
    for s in ("Don't rotate the logo.", "The S-pin must always be filled, not a stroke squircle.",
              "Don't use the wordmark without the logo."):
        assert Q.image_precondition(_r(s))[0] == "brand_mark"


def test_type_rules_need_words_in_the_image():
    assert Q.image_precondition(_r("Set headlines in Gilroy Extrabold."))[0] == "has_copy"
    # the contrast rule (machine params) still asks about any words, a wordmark included
    assert Q.image_precondition(_r("Text must reach 4.5:1 contrast.", params={"min_contrast": 4.5}))[0] == "has_text"


def test_other_visual_rules_have_no_gate():
    assert Q.image_precondition(_r("Retain a similar balance of maximum Swiggy Orange followed by Salt and White.")) is None


def test_print_specs_are_not_judged_from_pixels():
    assert not Q.image_checkable(_r("Use auto kerning when setting type."))
    assert not Q.image_checkable(_r("Set body copy in Gilroy Light at 15pt with 21pt leading."))
    assert Q.image_checkable(_r("Don't rotate the logo."))
    # a rule with real machine params is never dropped by its wording
    assert Q.image_checkable(_r("Keep leading tight", params={"min_contrast": 4.5}))


def test_type_rule_passes_on_a_logo_only_image(monkeypatch):  # noqa: ANN001
    """Live check (8 Nov): a badge logo alone passed "has_text" (its ring text is words) and a headline-typeface
    rule flagged it at 0.84. Type rules now ask whether there are words OUTSIDE any logo."""
    import asyncio
    from pathlib import Path

    from app.pipeline import engine as E
    from app.pipeline.context import CheckContext, UnitIn
    from app.pipeline.jev import CallLog
    from fakes import FakeJev, install

    logo = str(Path(__file__).resolve().parents[1] / "app" / "seed" / "logo.png")
    rule = _r("Headlines are written in sentence case.")  # a named typeface is not judged from pixels at all
    for copy, want in ((0.02, []), (0.98, ["R-X"])):
        install(monkeypatch, FakeJev(rules={"R-X": 0.84, "has_copy": copy, "has_text": 0.99}))
        ctx = CheckContext(channel="instagram", rules=[rule], version_kind="image")
        res = asyncio.run(E.analyze([UnitIn(id=1, kind="image", idx=0, image_path=logo)], ctx, calls=CallLog(), use_llm=False))
        assert [d.rule_code for d in res.findings] == want


def test_named_typeface_rules_are_not_judged_from_pixels():
    assert not Q.image_checkable(_r("Set subheads in Gilroy Medium."))
    assert not Q.image_checkable(_r("Set headlines in Gilroy Extrabold."))
    assert Q.image_checkable(_r("Use sentence case for headlines, subheads, and body."))


def test_unsure_visual_rules_on_one_image_become_one_card(monkeypatch):  # noqa: ANN001
    """Real-brand test (8 Nov): ~17 "needs a human look" cards per blog image from brand-book logo rules."""
    import asyncio
    from dataclasses import replace
    from pathlib import Path

    from app.pipeline import engine as E
    from app.pipeline.context import CheckContext, UnitIn
    from app.pipeline.jev import CallLog
    from fakes import FakeJev, install

    logo = str(Path(__file__).resolve().parents[1] / "app" / "seed" / "logo.png")
    base = _r("Retain a similar balance of orange and white.")  # no precondition gate
    rules = [replace(base, code=f"R-{i}") for i in range(5)]
    install(monkeypatch, FakeJev(rules={"R-0": 0.9, "R-1": 0.5, "R-2": 0.45, "R-3": 0.6, "R-4": 0.1}))
    ctx = CheckContext(channel="instagram", rules=rules, version_kind="image")
    res = asyncio.run(E.analyze([UnitIn(id=1, kind="image", idx=0, image_path=logo)], ctx, calls=CallLog(), use_llm=False))
    flags = [d for d in res.findings if d.band == "flag"]
    unsure = [d for d in res.findings if d.band == "human"]
    assert [d.rule_code for d in flags] == ["R-0"]
    assert len(unsure) == 1 and unsure[0].rule_code is None and "unsure about 3 visual rules" in unsure[0].explanation
    assert unsure[0].explanation.index("R-3") < unsure[0].explanation.index("R-1")  # most likely first
