"""Every Jev question, criterion and threshold in one place, so people can audit and edit them.
OWNER: pipeline-agent. Follows .claude/skills/typesafe-ai/SKILL.md:

- Jev answers judgments; code decides (bands, statuses, counts, dates and colours live in code).
- One judgment per question; refer to state fields by backticked path (`text`, `channel`).
- A Noul's `false` criterion describes a concrete OK situation (the rule's examples_ok).
- A Choice always picks an option, so it gets a companion Noul for the null case.
- State is only what the question needs: the unit text and the channel.

Convention: rule Nouls are high = problem. The self-check and fact-support Nouls are high = good
(`support_*`, `same_topic`, `keeps_cta`), and are named so.
"""
from __future__ import annotations

import re

from typing import Any, Optional

from typesafe_sdk import Choice, Noul, Score

from app.pipeline.context import FactSpec, RuleSpec

# ------------------------------------------------------------------ thresholds (code decides)
FLAG_AT = 0.70  # Noul p >= FLAG_AT  -> band "flag"   (per-category overrides: eval/results/thresholds.json)
HUMAN_AT = 0.30  # Noul p >= HUMAN_AT -> band "human"; below -> "pass"
HAS_CLAIM_AT = 0.70  # read `claim_type` only when has_claim > this
SUPPORT_AT = 0.70  # a fact supports a claim when p >= this
MEANING_OK_AT = 0.70  # a fix is acceptable only when meaning_kept >= this
SELFCHECK_RULE_MAX = HUMAN_AT  # after a fix, the rule's Noul must be below this ("pass")
PRECONDITION_AT = 0.70  # image companion Nouls (e.g. "is there a logo?") must be above this
CTA_AT = 0.70  # a sentence counts as a call to action above this
MIN_WORDS_FOR_JEV = 5  # shorter units (subject lines, "Order now.") get no rule/tone judgments: too little to judge

IMAGE_HEADERS = {"TypeSafe-Danger-Zone-Images": "true"}  # every request that carries an image or a Box

# ------------------------------------------------------------------ state builders
def text_state(text: str, channel: str) -> dict[str, Any]:
    return {"text": text, "channel": channel}


def revision_state(original: str, revised: str, channel: str) -> dict[str, Any]:
    return {"original": original, "revised": revised, "channel": channel}


def claim_state(claim_text: str) -> dict[str, Any]:
    return {"text": claim_text}


# ------------------------------------------------------------------ rule questions
def _examples(xs: list[str], n: int = 2) -> str:
    return "; ".join(f"“{x}”" for x in xs[:n] if x)


def rule_question(rule: RuleSpec, field: str = "text") -> Noul:
    """High = `field` breaks the rule. The rule's own examples anchor both outcomes."""
    bad, ok = _examples(rule.examples_violating), _examples(rule.examples_ok)
    true = f"`{field}` breaks the rule, like these examples: {bad}" if bad else f"`{field}` breaks the rule."
    false = (f"`{field}` follows the rule, like these on-brand examples: {ok}" if ok
             else f"Nothing in `{field}` goes against the rule.")
    kind = "advertising rule" if (rule.params or {}).get("citation") else "brand rule"  # rule packs cite a regulation
    return Noul(instructions=f"Does `{field}` break this {kind}: {rule.statement}", criteria={"true": true, "false": false})


# Image rules whose statement is about colour values, logo authenticity or font files are judged in code
# (palette ΔE, P1 logo fidelity, PDF fonts), never by Jev: Jev can't compare colour values reliably and
# has no copy of the official logo file.
IMAGE_RULES_IN_CODE = ("palette_delta_e", "palette_file", "logo_file", "logo_required", "fonts_file")

# Plainer, observable wording for image rules whose statement is numeric. Keyed by a params key.
IMAGE_RULE_WORDING: dict[str, tuple[str, str, str]] = {
    "min_contrast": (
        "Is any text in `image` hard to read because it blends into its background?",
        "Some words in `image` are faint or low-contrast against what is behind them, like light grey text on a cream banner.",
        "Every word in `image` stands out clearly, like deep coffee brown text on cream.",
    ),
}

# "Is there a logo at all?" Companion Noul for R-VIS-03 (clear space) and an input to P1 logo fidelity
# (logo.decide): a visible logo that doesn't match the approved file geometrically = redrawn/altered.
HAS_LOGO = Noul(
    instructions="Does `image` show a brand logo?",
    criteria={"true": "`image` shows a logo, such as a cup symbol with a brand name.",
              "false": "`image` shows no logo at all."})

# Companion Nouls: an image rule is only read when its precondition holds (null case in code).
IMAGE_PRECONDITIONS: dict[str, tuple[str, Noul]] = {
    "min_contrast": ("has_text", Noul(
        instructions="Does `image` contain written words?",
        criteria={"true": "`image` shows words, such as a headline, a price or a slogan.",
                  "false": "`image` is only a picture, with no words in it."})),
    "logo_clear_space_ratio": ("has_logo", HAS_LOGO),
}


def image_rule_question(rule: RuleSpec) -> Noul:
    for key, (instr, true, false) in IMAGE_RULE_WORDING.items():
        if key in (rule.params or {}):
            return Noul(instructions=instr, criteria={"true": true, "false": false})
    return rule_question(rule, field="image")


# Rules written in plain words (e.g. imported from a guideline PDF) carry no params, so the precondition is
# read from the statement: a logo rule ("Don't rotate the logo") only applies to an image that shows a logo,
# a type rule ("Set headlines in Gilroy") only to an image with words. Real-brand test (8 Nov): without this
# gate, 25 logo rules asked of every photo on a blog page gave ~60 "needs a human look" findings at 30-64%.
BRAND_MARK = Noul(
    instructions="Does `image` show a company logo or brand mark?",
    criteria={"true": "`image` shows a logo: a brand symbol, an app icon or a brand name set as a wordmark.",
              "false": "`image` shows no logo: only photos, products, people, scenery or ordinary text."})
# Type rules ("Set headlines in Gilroy") are about the copy set in an image, not the letters of a logo: a
# logo-only image passed "has_text" (its wordmark is words) and was flagged at 0.84 for its headline typeface.
HAS_COPY = Noul(
    instructions="Does `image` contain written words outside of any logo?",
    criteria={"true": "`image` shows copy set in type apart from the logo, such as a headline, a price, an offer or a slogan.",
              "false": "`image` has no words, or every word in it is part of a logo: a wordmark, or the brand name and "
                       "line around a badge."})
_LOGO_WORDS = re.compile(r"\b(logo|logotype|wordmark|word mark|brand mark|s-pin|squircle|lockup|emblem)\b", re.IGNORECASE)
_TYPE_WORDS = re.compile(r"\b(headlines?|typeface|font|type size|body copy|sub-?heads?|typography|sentence case|title case)\b",
                         re.IGNORECASE)
# Production specs that cannot be read from pixels (kerning, leading, point sizes, print colour codes).
_NOT_FROM_PIXELS = re.compile(r"\b(kerning|leading|\d+\s?pt\b|cmyk|pantone)", re.IGNORECASE)


def image_checkable(rule: RuleSpec) -> bool:
    """False for print/production specs a picture cannot show; those rules are left to people."""
    plain = not (set(rule.params or {}) - {"imported_from"})  # written in words, no machine params
    return not (plain and _NOT_FROM_PIXELS.search(rule.statement))


def image_precondition(rule: RuleSpec) -> Optional[tuple[str, Noul]]:
    for key, pair in IMAGE_PRECONDITIONS.items():
        if key in (rule.params or {}):
            return pair
    if _LOGO_WORDS.search(rule.statement):
        return ("brand_mark", BRAND_MARK)
    if _TYPE_WORDS.search(rule.statement):
        return ("has_copy", HAS_COPY)
    return None


def gated_probability(p: float, gate: float) -> float:
    """Truth table for a rule read only when its companion Noul (precondition) holds: clearly not -> 0 (pass);
    unsure -> at most "needs a human look"; clearly yes -> the rule's own probability."""
    if gate < HUMAN_AT:
        return 0.0
    if gate < PRECONDITION_AT:
        return min(p, FLAG_AT - 0.01)
    return p


# ------------------------------------------------------------------ own-site context (text rules)
# Disguised advertising (IN-CCPA-05, params.needs_outside_voice) means brand content posing as someone else's:
# a news story, an independent review, a customer's own post. A press note or blog post on the brand's OWN site,
# under its own name, poses as nothing. Real-brand test (8 Nov): 11 paragraphs of a brand's dated press note on
# its own blog were flagged at p 0.72-0.86. Code knows where the page is published (webpage.brand_owns_host), so
# on the brand's own site this companion Noul is asked in the same call, and code reads the rule only when it
# holds (gated_probability). Off the brand's site (or for posts with no known host) nothing changes.
OUTSIDE_VOICE = "outside_voice"


def outside_voice_question(brand: str, host: str) -> Noul:
    """High = `text` speaks as someone other than the brand. Where it is published goes in this question only."""
    b = re.sub(r"\s*\([^)]*\)", "", brand or "").strip() or "the brand"  # "Acme (demo brand)" -> "Acme"
    return Noul(
        instructions=f"`text` is a paragraph published on {host}, {b}'s own website. Is `text` written as if by "
                     f"someone other than {b}, such as a customer, an independent reviewer or a journalist?",
        criteria={
            "true": f"`text` itself poses as an outsider's own words about {b}: a customer's review or testimonial "
                    "written in their voice (“Honest review from a regular customer, not sponsored”), or an "
                    "independent reporter's news story.",
            "false": f"`text` is {b} speaking about itself under its own name: a dated press note (“Mumbai, 3 March "
                     f"2026: {b} today announced …”), its own blog post, or other people's words "
                     f"that {b} quotes and names as a customer's or its team's.",
        },
    )


def needs_outside_voice(rule: RuleSpec) -> bool:
    return bool((rule.params or {}).get("needs_outside_voice"))


def post_level(rule: RuleSpec) -> bool:
    """A rule about the post as a whole (a disguised ad, a missing "Ad" label): judged per paragraph, but
    reported once per post (engine.post_level_drafts), at the paragraph Jev rates highest."""
    return bool((rule.params or {}).get("post_level"))


# ------------------------------------------------------------------ tone + claims
TONE_DESCRIPTIONS = {
    "warm": "Friendly and welcoming, like a barista greeting a regular.",
    "confident": "Sure of itself and clear, without hedging.",
    "playful": "Light and fun, with a wink.",
    "formal": "Stiff and official, like a notice or a contract.",
    "pushy": "Pressures the reader to buy now or makes them feel guilty for waiting.",
    "sarcastic": "Says the opposite of what it means to mock someone or something.",
}


def allowed_tones(rule: RuleSpec) -> list[str]:
    return [str(t) for t in (rule.params or {}).get("tone_labels_allowed", [])]


def uses_tone_choice(rule: RuleSpec) -> bool:
    """Rules like "must sound warm and confident" (params.tone_labels_allowed) are judged with the
    `tone` Choice instead of a Noul: a literal "must" question flags any neutral sentence."""
    return bool(allowed_tones(rule))


def tone_violation(rule: RuleSpec, probabilities: dict[str, float], reported_elsewhere: set[str] | None = None) -> float:
    """Probability that the tone is outside the allowed labels (code, from the Choice distribution).
    Tones that another rule forbids (e.g. "pushy" -> R-TONE-02) are left to that rule, so one
    pushy line isn't reported twice."""
    ok = set(allowed_tones(rule)) | set(reported_elsewhere or ())
    return max(0.0, min(1.0, 1.0 - sum(p for label, p in probabilities.items() if label in ok)))


def forbidden_tones(rules: list[RuleSpec]) -> set[str]:
    return {str(t) for r in rules for t in (r.params or {}).get("tone_labels_forbidden", [])}


def tone_question(labels: list[str]) -> Choice:
    return Choice(instructions="Which tone best describes `text`?",
                  criteria={label: TONE_DESCRIPTIONS.get(label) for label in labels})


HAS_CLAIM = Noul(
    instructions="Does `text` state something about the product or the company that a customer could check as true or false?",
    criteria={
        "true": "`text` states a checkable fact, such as a price, rating, number, award, origin, offer or a comparison with others.",
        "false": "`text` only invites, describes a feeling or gives an opinion, like “Slow mornings, steady brews.”",
    },
)

CLAIM_TYPE = Choice(
    instructions="What kind of claim does `text` make?",
    criteria={
        "objective": "A specific fact that records could prove, such as a price, number, rating, award or origin.",
        "comparative": "Says the brand is better, cheaper or more popular than others, or the best.",
        "puffery": "Praise nobody could measure, such as “a cup you will love”.",
    },
)


# Check-worthiness gate (claims.gate): asked in the claim's own Jev call, next to the fact-support Nouls.
# State {"text": claim clause}; the paragraph it comes from goes in these two questions only (the support
# questions judge the clause alone). Code maps the answers to an outcome; see the claims.py docstring.
KIND_COMPARATIVE = "comparative_or_superlative"
KIND_SERVICE = "service_promise"
KIND_HEALTH = "health_or_safety"
KIND_FIRST_PARTY = "first_party_statistic"
KIND_PUFFERY = "opinion_or_puffery"
CLAIM_KINDS: dict[str, str] = {  # first option = the safe default (keeps today's behaviour)
    KIND_COMPARATIVE: "Ranks the brand or product above others or calls it the best, like “#1 in India”, "
                      "“the quickest delivery app” or “3 times more than other brands”.",
    KIND_SERVICE: "Promises the customer something about the product, service or offer: a delivery time, a price, "
                  "a discount, something free, a guarantee, what the product contains, or where it is available, "
                  "like “delivered in 20 minutes” or “flat 30% off”.",
    KIND_HEALTH: "Says the product affects health, safety or the body, like “good for your heart”, “boosts immunity” "
                 "or “safe for babies”.",
    KIND_FIRST_PARTY: "Reports the brand's own records about its orders, customers or sales, like “1 in 40 orders "
                      "included ice cream”, “our busiest hour was 8 pm” or “orders in Nagpur doubled this year”.",
    KIND_PUFFERY: "Praise, excitement or a feeling that nobody could measure, like “a truly amazing year” or "
                  "“everyone loves it”.",
}
CLAIM_RECORD_AT = FLAG_AT  # claim_record >= this: the brand's own past data (claims.is_first_party)
CLAIM_IS_CLAIM_AT = FLAG_AT  # claim_checkable >= this: a factual claim; HUMAN_AT..this: unsure; below HUMAN_AT: not a claim


def _from_paragraph(paragraph: Optional[str]) -> str:
    return f" `text` comes from this paragraph: “{paragraph}”" if paragraph and paragraph.strip() != "" else ""


def claim_kind_question(paragraph: Optional[str] = None) -> Choice:
    return Choice(instructions=f"What kind of statement is `text`?{_from_paragraph(paragraph)}",
                  criteria=dict(CLAIM_KINDS))


def claim_checkable_question(paragraph: Optional[str] = None) -> Noul:
    """Companion Noul for claim_kind (null case: nothing to check). High = a factual claim (named so)."""
    return Noul(
        instructions=f"Does `text` state something that records or tests could prove true or false?{_from_paragraph(paragraph)}",
        criteria={
            "true": "`text` states a checkable fact, such as a number, a price, a delivery time, a ranking, a result "
                    "or what a product contains.",
            "false": "`text` only tells a story, sets a mood, gives an opinion or invites the reader, like "
                     "“Handmade with love” or “What a year it was!”",
        },
    )


def claim_record_question(paragraph: Optional[str] = None) -> Noul:
    """A separate Noul, not a Choice option: a statement can be a record AND read like a ranking or a promise
    (“the fastest delivery happened in 3 minutes”), and a Choice would have to split between them.
    High = the brand reporting its own past data (named so)."""
    return Noul(
        instructions=f"Does `text` report something that already happened in the brand's own orders, customers or sales?"
                     f"{_from_paragraph(paragraph)}",
        criteria={
            "true": "`text` describes past results from the brand's own data, like “our busiest hour last year was 8 pm” "
                    "or “one customer ordered 300 times”.",
            "false": "`text` promises the reader something or compares the brand with others, like “delivered in 20 "
                     "minutes” or “India's favourite app”.",
        },
    )


def support_question(fact: FactSpec) -> Noul:
    """High = supported. One question per candidate fact, with the fact written into the question."""
    return Noul(
        instructions=f"Does this approved fact support the claim in `text`? Fact: {fact.statement}",
        criteria={
            "true": "The fact says the same thing that `text` claims, with the same numbers, names and offer details.",
            "false": "The fact is about something else, or its numbers, names or offer details differ from what `text` claims.",
        },
    )


def figure_support_question(fact: FactSpec, figure: str) -> Noul:
    """Atomic claims (claims.figure_questions): high = this fact confirms this one figure of a multi-figure claim.
    Asked only when code has seen the figure's digits in the fact."""
    return Noul(
        instructions=f"Does this approved fact confirm the figure “{figure}” in `text`? Fact: {fact.statement}",
        criteria={
            "true": f"The fact gives “{figure}” for the same thing that `text` uses it for, such as the same city, "
                    "product, count or time.",
            "false": f"The fact uses that number for something else, such as a different city, product or time than "
                     f"`text` gives for “{figure}”.",
        },
    )


def support_key(fact_code: str) -> str:
    return f"support_{fact_code}"


# ------------------------------------------------------------------ self-check
# The "same message and call to action" check is split into single judgments; code combines them:
# ok = same_topic >= MEANING_OK_AT and (original_has_cta < MEANING_OK_AT or keeps_cta >= MEANING_OK_AT)
SAME_TOPIC = Noul(
    instructions="Is `revised` about the same product, news or offer as `original`?",
    criteria={
        "true": "`revised` talks about the same thing as `original`, even if a word, claim or number was changed or removed.",
        "false": "`revised` talks about a different product, news or offer, or drops the main point of `original`.",
    },
)


def same_topic_question(post: Optional[str] = None) -> Noul:
    """SAME_TOPIC for one paragraph of a longer post: the post goes in this question only (a short line like
    "Order now." has no visible topic on its own; the other questions don't need the post)."""
    if not post:
        return SAME_TOPIC
    return Noul(instructions=f"Is `revised` about the same product, news or offer as `original`? Both are a paragraph of this post: “{post}”",
                criteria=SAME_TOPIC.criteria)


ORIGINAL_HAS_CTA = Noul(
    instructions="Does `original` ask the reader to do something, such as visit, order, buy or subscribe?",
    criteria={"true": "`original` asks the reader to act, like “Order now.”",
              "false": "`original` only describes or informs, like “Our new roast is here.”"},
)
KEEPS_CTA = Noul(
    instructions="Does `revised` ask the reader to do the same thing that `original` asks them to do?",
    criteria={"true": "Both ask for the same action, like visiting a cafe or ordering online.",
              "false": "`revised` asks for a different action, or no longer asks the reader to do anything."},
)
MEANING_QUESTIONS = {"same_topic": SAME_TOPIC, "original_has_cta": ORIGINAL_HAS_CTA, "keeps_cta": KEEPS_CTA}

# Asked about every LLM fix: a rewrite must not swap one unsupported ranking claim for another
# ("#1 coffee" -> "India's favourite coffee"). The rewritten words go in the question (other sentences of
# `revised` are judged by their own findings). High = problem; code rejects the fix unless it lands in "pass".
def new_superlative_question(rewrite: str) -> Noul:
    return Noul(
        instructions=f"Does this rewritten text in `revised` rank the brand or its product above others? Text: “{rewrite}”",
        criteria={
            "true": "It calls the product the best, a favourite, the leader, top, most loved, number one or better than "
                    "every other coffee, like “India's favourite coffee” or “the leading filter blend”.",
            "false": "It describes the product without ranking it, like “a smooth filter blend roasted in Pune”.",
        },
    )


def meaning_kept(nouls: dict[str, float], removal_required: bool = False) -> bool:
    """Truth table for 'the fix keeps the message and the call to action'.
    `removal_required` (code-decided: the text relies on an EXPIRED fact, e.g. an ended offer, which the
    fix must remove) waives same_topic, since dropping that offer is the point; the call to action must stay."""
    if not removal_required and nouls.get("same_topic", 0.0) < MEANING_OK_AT:
        return False
    return nouls.get("original_has_cta", 1.0) < MEANING_OK_AT or nouls.get("keeps_cta", 0.0) >= MEANING_OK_AT


def meaning_kept_split(unit: dict[str, float], post: dict[str, float], removal_required: bool = False) -> bool:
    """Same truth table with the CTA asked twice: about the paragraph (`unit`) and the whole post (`post`).
    The CTA is kept if either says so: the paragraph keeps its own action, or the post still asks for it elsewhere
    (a post has one CTA, so a fix may drop a second one). same_topic comes from the paragraph."""
    if not removal_required and unit.get("same_topic", 0.0) < MEANING_OK_AT:
        return False
    return any(n.get("original_has_cta", 1.0) < MEANING_OK_AT or n.get("keeps_cta", 0.0) >= MEANING_OK_AT
               for n in (unit, post) if "keeps_cta" in n)


# ------------------------------------------------------------------ call to action (counted in code)
CTA_PARAM_KEYS = ("min_ctas", "max_ctas")


def cta_question(sentence: str) -> Noul:
    return Noul(
        instructions=f"Does this sentence from `text` ask the reader to do something? Sentence: “{sentence}”",
        criteria={"true": "It asks the reader to act, such as visit, order, buy, subscribe, follow, share or tag a friend.",
                  "false": "It only describes or informs, like “Our new roast is here.”"},
    )


def multi_cta_question(sentence: str) -> Noul:
    return Noul(
        instructions=f"Does this sentence from `text` ask the reader to do two or more different things? Sentence: “{sentence}”",
        criteria={"true": "It lists several actions, like “Visit us, order online and follow us.”",
                  "false": "It asks for one action or none, like “Order the gift box online.”"},
    )


def question_text(q: Any) -> dict[str, Any]:
    """Plain dict view of a question (for docs, DECISIONS.md and tests)."""
    return q.model_dump() if hasattr(q, "model_dump") else dict(q)


# ------------------------------------------------------------------ P1-1 audience preview
# Simulated readers: brand-defined reader groups (seed audiences.json) or, without that file, the six
# Nemotron-Personas-India segments with the generic traits below. Each reader is written into its own
# question, reader first (who they are, what they need, language and digital habits, what makes them
# trust or doubt), then the question. State: {"source": who publishes it (audience_source), "content": masked
# post text} or, for an image post, {"source", "image": Image, "caption": masked caption}. Code turns the
# answers into scores and shares (audience.py).
AUDIENCE_CONFUSED_AT = 0.70  # `confused` Noul above this: this reader would misunderstand the main message
AUDIENCE_NOT_FOR_THEM_AT = 0.70  # `not_for_them` Noul above this: the post isn't relevant to this reader
AUDIENCE_TRUST_MAX = 2.5  # credibility (1-5) at or below this: leans towards believing it  (bands: see DECISIONS)
AUDIENCE_DOUBT_MIN = 3.5  # at or above this: leans towards doubting it; in between: unsure
AUDIENCE_CULPRIT_AT = 0.70  # `any_distrust` companion: report the culprit paragraph above this ...
AUDIENCE_CULPRIT_MAYBE_AT = 0.30  # ... as "possible" between the two, and not at all below

# Three judgments per reader, each one condition (skill: "one judgment per question"):
#   credibility  Score  how believable and honest the post seems to this reader (NOT whether they would act,
#                       NOT whether the product suits them: both used to be mixed in and made every ad look
#                       doubted, 8 Nov);
#   not_for_them Noul   is the post outside this reader's needs (relevance, reported separately);
#   confused     Noul   would this reader misunderstand the main message.
# The five credibility levels, in Score order (level index 0..4 = credibility 1..5). Each is a situation that
# reads on its own (skill: "describe each Score level as a situation"). Level 1 names the case that caused
# confusion before: a reader can believe a post about something they don't want.
# Calibration (8 Nov, app/eval/audience_calibration.json): the levels are about the post's MAIN message.
# Before, level 4 was "parts of the post are exaggerated ... a number that sounds too strong" and nothing
# between "would check one detail" and that level fitted "I believe it, the wording is just excited", so a
# playful, honest year-in-review full of the brand's own order figures (and every long page, where some part
# always sounds strong) landed on level 4. Now excited wording or a big-sounding number in a post the reader
# believes is level 2; level 4 needs the central claim itself to seem too strong to be true.
AUDIENCE_CREDIBILITY_LEVELS = (
    "This reader believes the post: what it says about the brand, its products, its offer or its customers "
    "seems true to them, whether or not they want it themselves.",
    "This reader believes the post's main message but finds part of it overdone, such as excited wording or a "
    "big-sounding number, or would check one detail such as a rating or a condition.",
    "This reader can't tell whether the post's main message is true.",
    "This reader doubts the post's main message: its central claim, number or promise seems too strong to be "
    "true.",
    "This reader thinks the post is spam, a scam or deliberately misleading, and would not believe it.",
)
AUDIENCE_LEVEL_LABELS = ("Believes it", "Mostly believes it", "Can't tell", "Seems exaggerated",
                         "Seems misleading")

# Who published the post (code knows it: every item in Rulemint is the brand's own content, and its channel
# is stored). A real reader always sees where a post comes from; without it Jev judged an anonymous text.
AUDIENCE_SOURCE_BY_CHANNEL = {
    "instagram": "Published by the brand itself on its own Instagram account.",
    "linkedin": "Published by the brand itself on its own LinkedIn page.",
    "website": "Published by the brand itself on its own website.",
    "email": "An email the brand itself sends to its customers.",
}
AUDIENCE_SOURCE_DEFAULT = "Published by the brand itself on one of its own channels."


def audience_source(channel: Optional[str]) -> str:
    """The `source` state field: who publishes the post, and where."""
    return AUDIENCE_SOURCE_BY_CHANNEL.get(str(channel or "").lower(), AUDIENCE_SOURCE_DEFAULT)


def audience_subject(is_image: bool, has_source: bool = False) -> str:
    """"the post in `content`"; with has_source, followed by "(published by the brand itself, see `source`)"."""
    subj = "the post (`image` with its `caption`)" if is_image else "the post in `content`"
    return f"{subj} (published by the brand itself, see `source`)" if has_source else subj


def audience_credibility_question(reader: str, is_image: bool = False, has_source: bool = True) -> Score:
    """Reader first, then one judgment: how believable the post seems to this reader.
    `reader` comes from audience.reader_profile()."""
    return Score(
        instructions=f"Reader: {reader}\n\nHow believable and honest does {audience_subject(is_image, has_source)} "
                     "seem to this reader?",
        criteria=list(AUDIENCE_CREDIBILITY_LEVELS),
    )


def audience_not_for_them_question(reader: str, is_image: bool = False, has_source: bool = True) -> Noul:
    """Relevance, high = problem: the post is about something outside this reader's needs or life."""
    subj = audience_subject(is_image)
    return Noul(
        instructions=f"Reader: {reader}\n\nIs {audience_subject(is_image, has_source)} about something this reader "
                     "has no use for?",
        criteria={
            "true": f"What {subj} offers doesn't fit this reader's needs, budget or daily life, so they would scroll "
                    "past it even if they believe it, like a premium gift box for someone counting every rupee.",
            "false": f"What {subj} offers fits something this reader needs or does, like a coffee offer for someone "
                     "who buys coffee every week.",
        },
    )


# Confused: "main message" is what the post mainly tells the reader. The criteria used to ask whether the reader
# could say "what is offered or what they are asked to do", which a year-in-review or a news-style post (no
# offer, no ask) can never answer, so 37% of readers were "confused" by a long, clear press note (8 Nov).
def audience_confused_question(reader: str, is_image: bool = False, has_source: bool = True) -> Noul:
    subj = audience_subject(is_image)
    return Noul(
        instructions=f"Reader: {reader}\n\nWould this reader misunderstand the main message of "
                     f"{audience_subject(is_image, has_source)}?",
        criteria={
            "true": f"After reading {subj}, this reader could not say in one sentence what it is mainly telling them, "
                    "for example because of jargon, abbreviations, mixed messages, or a language or format they "
                    "find hard to read.",
            "false": f"After reading {subj}, this reader could say in one sentence what it is mainly telling them, "
                     "like “there is a new app and I can get it from the link” or “the company is sharing what "
                     "its customers did this year”.",
        },
    )


# Generic traits for the six Nemotron segments, used only when the brand has no audiences.json.
# Keys match `segment` in seed/personas.json.
AUDIENCE_FALLBACK_GROUPS: dict[str, dict] = {
    "18-24 urban": {
        "label": "18–24, urban",
        "description": "Young adult in a city, on Instagram and YouTube every day",
        "traits": {"needs": "things that fit a tight budget and feel current",
                   "trust_triggers": "friends' and creators' recommendations, real reviews, clear prices",
                   "doubt_triggers": "hype, fake-looking urgency, offers that sound too good to be true",
                   "language": "comfortable with English and Hinglish; skims quickly on a phone"}},
    "25-40 urban": {
        "label": "25–40, urban",
        "description": "Working adult in a city who shops online and compares before buying",
        "traits": {"needs": "value for money and saving time",
                   "trust_triggers": "specific facts, known brands, ratings with a source, clear terms",
                   "doubt_triggers": "vague superlatives, hidden conditions, pushy deadlines",
                   "language": "reads English at work; prefers short, plain messages"}},
    "25-40 tier2/3": {
        "label": "25–40, tier-2/3 and rural",
        "description": "Adult in a smaller town or village who uses WhatsApp and a budget smartphone",
        "traits": {"needs": "practical value and things available nearby",
                   "trust_triggers": "word of mouth, local shops, prices stated plainly",
                   "doubt_triggers": "big-city marketing language, English slogans, online-only offers",
                   "language": "prefers their regional language; English only for simple words"}},
    "41-60": {
        "label": "41–60",
        "description": "Middle-aged adult, careful with money, reads news and forwards on WhatsApp",
        "traits": {"needs": "reliability and a brand that has been around",
                   "trust_triggers": "clear facts, a reachable company, family recommendations",
                   "doubt_triggers": "exaggerated claims, slang, pressure to decide now",
                   "language": "reads English or a regional language slowly and carefully"}},
    "students": {
        "label": "Students",
        "description": "Student living on pocket money, heavy smartphone user",
        "traits": {"needs": "low prices, student offers, things useful for study and friends",
                   "trust_triggers": "peers' opinions, honest reviews, simple deals",
                   "doubt_triggers": "hidden charges, too-good-to-be-true discounts, adult-sounding hard sell",
                   "language": "comfortable with English, Hinglish and slang"}},
    "small_business": {
        "label": "Small-business owners",
        "description": "Runs a shop or small business; judges offers by margins and proof",
        "traits": {"needs": "dependable suppliers and clear costs",
                   "trust_triggers": "exact numbers, terms in writing, references from other traders",
                   "doubt_triggers": "unverifiable claims, missing prices, 'guaranteed' promises",
                   "language": "reads English or a regional language; prefers straight talk"}},
}


# Which paragraph hurts trust most: a Choice over the paragraphs (options named by their opening
# words, criteria = the paragraph), plus its companion Noul for the "none of them" case.
AUDIENCE_ANY_DISTRUST = Noul(
    instructions="Does any part of `content` make a typical reader less likely to trust the brand?",
    criteria={
        "true": "Some part of `content` makes a reader doubt the brand, such as an unbelievable claim, a made-up "
                "looking number, or pressure to buy now.",
        "false": "Every part of `content` reads as honest and plain, like an opening time, a plain description of "
                 "what is offered, or a friendly invitation.",
    },
)


def audience_culprit_question(options: dict[str, str]) -> Choice:
    """`options`: {descriptive option name: paragraph text}."""
    return Choice(instructions="Which paragraph of `content` would most reduce a reader's trust in the brand?",
                  criteria=dict(options))


# ------------------------------------------------------------------ Round 3: unclear-rule detection (pipeline/clarity.py)
# Jev is asked the SAME rule question in 2-3 wordings of the rule (the original + paraphrases) about one
# probe text, all in one call. Code measures how much the answers move between wordings: a clear rule
# gets the same answer whichever way it is phrased; an unclear one doesn't. Code decides the score.
# High = problem (the probe breaks the rule). One condition per question; only the rule wording varies.
CLARITY_FIELD_TEXT = "text"  # state {"text": probe}: a short post, caption or email line
CLARITY_FIELD_IMAGE = "image_description"  # visual rules: state {"image_description": probe}
CLARITY_INTENT_CHANGED_AT = 0.70  # rewrite intent check: above this the rewrite "may ask for something else"


def clarity_state(probe: str, field: str = CLARITY_FIELD_TEXT) -> dict[str, Any]:
    return {field: probe}


def clarity_rule_question(wording: str, field: str = CLARITY_FIELD_TEXT) -> Noul:
    """The rule question with no examples in the criteria: we measure the wording alone, as a writer reads it."""
    subject = f"`{field}`" if field == CLARITY_FIELD_TEXT else f"The image described in `{field}`"
    return Noul(
        instructions=f"Does `{field}` break this brand rule: {wording}",
        criteria={
            "true": f"{subject} goes against the rule as it is written.",
            "false": f"{subject} is something a brand reviewer would pass under this rule.",
        },
    )


def clarity_questions(wordings: list[str], field: str = CLARITY_FIELD_TEXT) -> dict[str, Noul]:
    """{w0: original, w1..: paraphrases}. Question ids are not sent to the model."""
    return {f"w{i}": clarity_rule_question(w, field) for i, w in enumerate(wordings)}


# Rewrite check (high = problem): does the suggested wording ask for something the original rule does not?
# State: {"original": old statement, "revised": suggested statement}.
CLARITY_INTENT_CHANGED = Noul(
    instructions="Does `revised` ask writers to do something that `original` does not ask for?",
    criteria={
        "true": "`revised` adds a new requirement or drops one, such as banning all humour when `original` "
                "only bans hurtful jokes.",
        "false": "`revised` asks for the same thing as `original`, only spelled out more concretely, such as "
                 "naming the kinds of jokes that count as hurtful.",
    },
)
