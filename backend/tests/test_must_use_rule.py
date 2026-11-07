"""Regression (real-brand test, 8 Nov): an imported rule "Always refer to the brand as 'Swiggy' ..." with no
params was read as a banned-word rule, so every correct "Swiggy" was flagged as a do-not-use word."""
from app.pipeline import tier0 as T
from app.pipeline.context import CheckContext, RuleSpec


def _ctx(*rules: RuleSpec) -> CheckContext:
    return CheckContext(rules=list(rules), terms=[], channel="instagram", version_kind="text")


def _rule(code: str, statement: str) -> RuleSpec:
    return RuleSpec(code=code, version=1, statement=statement, category="terminology", severity="major",
                    check_method="tier0", params={}, enabled=True)


MUST = _rule("R-1", "Always refer to the brand as 'Swiggy' in all media mentions, including TVCs, ad copy, and captions.")
NEVER = _rule("R-2", "Never say 'cheap'.")


def test_required_name_written_correctly_is_not_flagged():
    assert T.run_tier0_text("Instamart users searched for Swiggy (7,000) and more.", _ctx(MUST)) == []


def test_required_name_in_wrong_case_is_flagged_with_the_exact_spelling_as_fix():
    hits = T.run_tier0_text("Order on SWIGGY today, or swiggy it.", _ctx(MUST))
    assert [h.quote for h in hits] == ["SWIGGY", "swiggy"]
    assert all(h.fix == "Swiggy" and h.rule_code == "R-1" for h in hits)


def test_never_say_rule_still_bans_the_quoted_word():
    hits = T.run_tier0_text("A cheap coffee.", _ctx(NEVER))
    assert [h.quote for h in hits] == ["cheap"]


def test_must_use_detection():
    assert T.is_must_use(MUST) and not T.is_must_use(NEVER)
    assert not T.is_must_use(_rule("R-3", "Don't write the wordmark in all-caps."))
