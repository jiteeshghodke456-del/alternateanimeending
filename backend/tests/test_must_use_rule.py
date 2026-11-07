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


# Edge cases (8 Nov): links, emails, hashtags and @handles are names, not copy; possessives are part of the word.
def test_required_name_inside_links_emails_and_domains_is_not_flagged():
    text = "Read more at blog.swiggy.com/news or https://www.swiggy.com/offers, or write to press@swiggy.in."
    assert T.run_tier0_text(text, _ctx(MUST)) == []


def test_required_name_inside_hashtags_and_handles_is_not_flagged():
    assert T.run_tier0_text("Follow @swiggy and tag #swiggy or #swiggyinstamart.", _ctx(MUST)) == []


def test_lowercase_name_next_to_a_hashtag_is_still_flagged():
    hits = T.run_tier0_text("Order on swiggy #swiggy", _ctx(MUST))
    assert [h.quote for h in hits] == ["swiggy"]


def test_possessive_of_the_required_name():
    assert T.run_tier0_text("Swiggy's new app and Swiggy’s rider fleet.", _ctx(MUST)) == []
    hits = T.run_tier0_text("SWIGGY'S new app and swiggy’s fleet.", _ctx(MUST))
    assert [(h.quote, h.fix) for h in hits] == [("SWIGGY'S", "Swiggy's"), ("swiggy’s", "Swiggy’s")]


def test_apostrophe_in_the_statement_is_not_a_quote_mark():
    rule = _rule("R-4", "Always use the brand's name as 'Swiggy' in copy.")
    assert T.statement_words(rule.statement) == ["Swiggy"]
    assert T.is_must_use(rule)
    assert [h.quote for h in T.run_tier0_text("Order on SWIGGY.", _ctx(rule))] == ["SWIGGY"]
    never = _rule("R-5", "Don't use the brand’s old name ‘Bundl’ anymore.")
    assert T.statement_words(never.statement) == ["Bundl"]
    assert [h.quote for h in T.run_tier0_text("Bundl is now Swiggy Genie.", _ctx(never))] == ["Bundl"]
