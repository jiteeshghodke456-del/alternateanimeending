"""Regression (real-brand test, Mailchimp style guide, 8 Nov): quoted words in imported plain-language rules
were all treated as banned, so "Don't refer to an audience as "it"; always use "they."" banned "it" and
"they" and every rewrite failed its self-check; rules with no quoted words were never checked at all."""
from app.pipeline import tier0 as T
from app.pipeline.context import RuleSpec


def _r(statement: str) -> RuleSpec:
    return RuleSpec(code="R-X", category="terminology", severity="major", statement=statement, check_method="tier0",
                    params={"imported_from": "abc"})


def test_word_roles():
    assert T.word_roles('Don\'t refer to someone as a "ninja," "rockstar," or "wizard".') == (["ninja", "rockstar", "wizard"], [])
    assert T.word_roles("Always refer to the brand as 'Swiggy' in all media mentions.") == ([], ["Swiggy"])
    assert T.word_roles("Never say 'cheap'.") == (["cheap"], [])
    # pronouns are never a word list; formatting rules are judged as a whole
    assert T.word_roles('Don\'t refer to an audience as "it"; always use "they."') == ([], [])
    assert T.word_roles('Use the % symbol instead of spelling out "percent."') == ([], [])
    assert T.word_roles('Don\'t capitalize "audience" unless it\'s grammatically necessary.') == ([], [])


def test_rules_without_a_word_list_go_to_the_judgment_model_not_nowhere():
    assert not T.code_checkable(_r('Don\'t refer to an audience as "it"; always use "they."'))
    assert not T.code_checkable(_r("Use exclamation points sparingly, never more than one at a time."))
    assert T.code_checkable(_r('Don\'t call groups of people "guys."'))


def test_it_and_they_are_not_flagged_by_code():
    from app.pipeline.context import CheckContext
    ctx = CheckContext(channel="instagram", rules=[_r('Don\'t refer to an audience as "it"; always use "they."')])
    assert T.run_tier0_text("Pick a template and send it to the people who like what they read.", ctx) == []


def test_engine_asks_the_model_about_rules_code_cannot_check():
    from app.pipeline import engine as E
    from app.pipeline.context import CheckContext
    from app.pipeline.jev import CallLog

    r1, r2 = _r("Use exclamation points sparingly."), _r('Don\'t call groups of people "guys."')
    eng = E._Run([], CheckContext(channel="instagram", rules=[r1, r2]), CallLog(), use_jev=False, use_llm=False,
                   explain_findings=False, on_finding=None, on_progress=None)  # type: ignore[arg-type]
    assert [r.statement for r in eng.text_rules()] == ["Use exclamation points sparingly."]


def test_ordinals_are_not_claims_unless_they_rank():
    from app.pipeline.context import CheckContext
    ctx = CheckContext(channel="instagram", rules=[])
    assert [t.text for t in T.claim_triggers("Your 1st campaign doesn't have to be perfect.", [])] == []
    assert [t.text for t in T.claim_triggers("We are ranked 1st in India for delivery.", [])] == ["1"]


def test_fix_may_keep_a_superlative_it_was_not_asked_to_fix_when_no_rule_wants_evidence():
    import asyncio
    from app.pipeline import selfcheck as S
    from app.pipeline.context import CheckContext, UnitIn
    text = "HUGE NEWS!!! We are the #1 email tool."
    unit = UnitIn(id=1, kind="text", idx=0, text=text, char_start=0)
    ctx = CheckContext(channel="instagram", rules=[], post_text=text)
    ok = asyncio.run(S.verify_fix(unit, 0, len(text), "Big news: we are the #1 email tool.", None, ctx, use_jev=False))
    assert not any("superlative" in r for r in ok.reasons)
    added = asyncio.run(S.verify_fix(unit, 0, len(text), "Big news: we are the best email tool.", None, ctx, use_jev=False))
    assert any("superlative" in r for r in added.reasons)
