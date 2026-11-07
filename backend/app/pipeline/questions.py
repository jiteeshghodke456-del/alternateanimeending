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

from typing import Any, Optional

from typesafe_sdk import Choice, Noul

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
    return Noul(instructions=f"Does `{field}` break this brand rule: {rule.statement}", criteria={"true": true, "false": false})


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


def image_precondition(rule: RuleSpec) -> Optional[tuple[str, Noul]]:
    for key, pair in IMAGE_PRECONDITIONS.items():
        if key in (rule.params or {}):
            return pair
    return None


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


def support_question(fact: FactSpec) -> Noul:
    """High = supported. One question per candidate fact, with the fact written into the question."""
    return Noul(
        instructions=f"Does this approved fact support the claim in `text`? Fact: {fact.statement}",
        criteria={
            "true": "The fact says the same thing that `text` claims, with the same numbers, names and offer details.",
            "false": "The fact is about something else, or its numbers, names or offer details differ from what `text` claims.",
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
# Simulated readers (NVIDIA Nemotron-Personas-India, CC-BY-4.0). Each persona is written into its own
# question (skill: "put each item into its own question" rather than an id into a shared list).
# State: {"content": masked post text} or, for an image post, {"image": Image, "caption": masked caption}.
# High = problem. Code turns the probabilities into percentages and dots (audience.py).
AUDIENCE_DISTRUST_AT = 0.70  # p above this: this simulated reader distrusts / is confused
AUDIENCE_UNSURE_AT = 0.30  # 0.30..0.70: unsure
AUDIENCE_CULPRIT_AT = 0.70  # `any_distrust` companion: report the culprit paragraph above this ...
AUDIENCE_CULPRIT_MAYBE_AT = 0.30  # ... as "possible" between the two, and not at all below


def audience_subject(is_image: bool) -> str:
    return "the post (`image` with its `caption`)" if is_image else "`content`"


def audience_distrust_question(bio: str, is_image: bool = False) -> Noul:
    subj = audience_subject(is_image)
    return Noul(
        instructions=f"Would this person find {subj} misleading or hard to trust? Person: {bio}",
        criteria={
            "true": f"This person would doubt {subj}: a claim sounds too good to be true to them, the numbers or "
                    "promises seem made up, or the pressure to buy makes them suspicious.",
            "false": f"This person would take {subj} at face value, like a plain note that a product costs Rs 495 "
                     "including taxes or that a shop opens at 8 am.",
        },
    )


def audience_confused_question(bio: str, is_image: bool = False) -> Noul:
    subj = audience_subject(is_image)
    return Noul(
        instructions=f"Would this person misunderstand the main message of {subj}? Person: {bio}",
        criteria={
            "true": f"After reading {subj}, this person could not say what is being offered or what they are asked "
                    "to do, for example because of slang, codes, mixed messages or English they find hard.",
            "false": f"After reading {subj}, this person could say in one sentence what is offered and what to do "
                     "next, like “a new product is out and I can order it online”.",
        },
    )


# Which paragraph hurts trust most: a Choice over the paragraphs (options named by their opening
# words, criteria = the paragraph), plus its companion Noul for the "none of them" case.
AUDIENCE_ANY_DISTRUST = Noul(
    instructions="Does any part of `content` make a typical reader less likely to trust the brand?",
    criteria={
        "true": "Some part of `content` makes a reader doubt the brand, such as an unbelievable claim, a made-up "
                "looking number, or pressure to buy now.",
        "false": "Every part of `content` reads as honest and plain, like an opening time, a price with taxes or "
                 "a friendly invitation to visit.",
    },
)


def audience_culprit_question(options: dict[str, str]) -> Choice:
    """`options`: {descriptive option name: paragraph text}."""
    return Choice(instructions="Which paragraph of `content` would most reduce a reader's trust in the brand?",
                  criteria=dict(options))
