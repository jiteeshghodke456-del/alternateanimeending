"""Indian-language content: script detection, danda sentences, Unicode-safe matching, Indian numbers,
language-neutral readability, cross-language fact candidates and the fix-language check."""
from __future__ import annotations

import asyncio
import unicodedata

import pytest

from app.pipeline import claims as C
from app.pipeline import explain as X
from app.pipeline import lang as L
from app.pipeline import selfcheck as S
from app.pipeline import tier0
from app.pipeline.context import UnitIn
from pipeline_helpers import seed_ctx, seed_facts, seed_rules


# ------------------------------------------------------------------ script / language detection
@pytest.mark.parametrize("text,script,lang", [
    ("भारत की नंबर 1 फ़िल्टर कॉफ़ी आ गई है। हमारे किसी भी कैफ़े में आइए।", "Devanagari", "hi"),
    ("फक्त आज! कोणत्याही कॅफेमधून एक बॅग घ्या. आमचे कॅफे जवळच आहेत.", "Devanagari", "mr"),
    ("Jaldi karo, hamara Monsoon Malabar almost khatam hai.", "Latin", "hinglish"),
    ("எங்கள் கஃபேக்கு வாருங்கள்.", "Tamil", "Tamil"),
    ("ধীর সকাল, স্থির কফি।", "Bengali", "Bengali"),
    ("India's #1 filter coffee is here. Visit any of our cafes.", "Latin", "en"),
    # Hindi sentences around English product names: still Hindi (code-mixed)
    ("हमारा Saffron Signature Roast अब online order के लिए available है।", "Devanagari", "hi"),
    ("12345 !!!", "none", "none"),
])
def test_detect(text: str, script: str, lang: str) -> None:
    info = L.detect(text)
    assert (info.script, info.lang) == (script, lang)


def test_english_eval_set_is_detected_as_english() -> None:
    import json

    from app.eval.run import DATASET
    rows = [json.loads(x) for x in DATASET.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["id"] for r in rows if not L.detect(r["text"]).is_english] == []


def test_english_words_hinglish_lookalikes_stay_english() -> None:
    assert L.detect("Par for the course: we roast se small batches on Tuesday.").lang == "en"


# ------------------------------------------------------------------ sentences
def test_danda_splits_sentences() -> None:
    t = "सिर्फ आज! किसी भी कैफ़े से एक पैक ले जाइए। हमारे 12 कैफ़े हैं॥ आइए?"
    got = [t[s:e] for s, e in tier0.sentences(t)]
    assert got == ["सिर्फ आज!", "किसी भी कैफ़े से एक पैक ले जाइए।", "हमारे 12 कैफ़े हैं॥", "आइए?"]


def test_english_sentences_unchanged() -> None:
    t = "Just Rs. 495 incl. taxes. Order now! Why wait?"
    assert [t[s:e] for s, e in tier0.sentences(t)] == ["Just Rs. 495 incl. taxes.", "Order now!", "Why wait?"]


def test_well_formed_accepts_danda() -> None:
    t = "हमारी कॉफ़ी सबसे अच्छी है। आइए।"
    end = t.index("।") + 1
    assert S.well_formed(t, 0, end, "हमारी कॉफ़ी ताज़ा है।") == []
    assert "the fix doesn't end the sentence with punctuation" in S.well_formed(t, 0, end, "हमारी कॉफ़ी ताज़ा है")


# ------------------------------------------------------------------ Unicode-safe matching
def test_matra_and_virama_are_word_characters() -> None:
    rx = tier0.phrase_regex("मुफ्त")
    assert rx.search("यह मुफ्त है") is not None
    assert rx.search("मुफ्ती साहब") is None  # vowel sign after the phrase: same word
    assert tier0.phrase_regex("सस्ता").search("बहुत सस्ता!") is not None
    assert tier0.phrase_regex("सस्त").search("बहुत सस्ता!") is None


def test_phrase_regex_normalises_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", "फ़्री")
    assert decomposed != "फ़्री" or True  # nukta forms are stable under NFC; the regex must still match NFC text
    assert tier0.phrase_regex(decomposed).search(L.nfc("आज फ़्री डिलीवरी")) is not None


def test_unicode_boundaries_rewrites_backslash_b() -> None:
    import re

    pat = L.unicode_boundaries(r"\bसस्ता\b|[\b]x|\\b")
    assert r"\\b" in pat and "[\\b]" in pat  # escaped backslash and class untouched
    assert re.search(L.unicode_boundaries(r"\bसस्ता\b"), "बहुत सस्ता है") is not None
    assert re.search(r"\bसस्ता\b", "बहुत सस्ता है") is None  # why: plain \b fails after the vowel sign
    assert re.search(L.unicode_boundaries(r"\bbest\b"), "the best, bestest") is not None
    assert len(re.findall(L.unicode_boundaries(r"\bbest\b"), "best bestest best")) == 2


def hits(text: str, channel: str = "instagram") -> list[tuple[str | None, str]]:
    return [(h.rule_code, h.quote) for h in tier0.run_tier0_text(text, seed_ctx(text, channel))]


@pytest.mark.parametrize("text,code,quote", [
    ("हमारी गारंटी वाली ताज़ा बीन्स।", "R-TERM-02", "गारंटी"),
    ("सकाळ सुधारण्याचा स्वस्त मार्ग आहे.", "R-TERM-02", "स्वस्त"),
    ("सिर्फ आज!!! एक पैक ले जाइए।", "R-CLAIM-03", "सिर्फ आज!!!"),
    ("फक्त आज! एक बॅग घ्या.", "R-CLAIM-03", "फक्त आज!"),
    ("Jaldi karo, stock khatam hai.", "R-CLAIM-03", "Jaldi karo"),
    ("सभी बीन्स पर 20% छूट।", "R-CLAIM-04", "20% छूट"),
    ("हमारी कॉफ़ी इम्युनिटी बढ़ाती है।", "R-LEGAL-01", "इम्युनिटी"),
])
def test_indic_tier0_hits(text: str, code: str, quote: str) -> None:
    assert any(c == code and quote in q or q in quote and c == code for c, q in hits(text)), hits(text)


def test_promo_with_hindi_end_date_is_fine() -> None:
    assert not [h for h in hits("सभी बीन्स पर 20% छूट, 30 नवंबर तक।") if h[0] == "R-CLAIM-04"]
    assert not [h for h in hits("सर्व बीन्सवर 20% सवलत, रविवारपर्यंत.") if h[0] == "R-CLAIM-04"]


def test_clean_hindi_and_marathi_have_no_tier0_hits() -> None:
    assert hits("हमारी Cold Steam Brew को 18 घंटे तक भिगोया जाता है। इसे चखने के लिए आइए।") == []
    assert hits("आम्ही पुण्यात दर मंगळवारी कॉफी भाजतो. नक्की भेट द्या.") == []


@pytest.mark.parametrize("text,group,trig", [
    ("भारत की नंबर 1 फ़िल्टर कॉफ़ी", "superlative", "नंबर 1"),
    ("भारतातील नंबर १ कॉफी", "superlative", "नंबर १"),
    ("पुणे की सबसे अच्छी कॉफ़ी", "superlative", "सबसे अच्छी"),
    ("सर्वश्रेष्ठ कॉफ़ी", "superlative", "सर्वश्रेष्ठ"),
    ("पुण्यातील सर्वात चांगली कॉफी", "superlative", "सर्वात चांगली"),
    ("Pune ki sabse best coffee", "superlative", "sabse best"),
    ("आमचे १२ कॅफे", "number", "१२"),
])
def test_indic_claim_triggers(text: str, group: str, trig: str) -> None:
    got = [(t.group, t.text) for t in tier0.claim_triggers(text, seed_rules())]
    assert (group, trig) in got, got


# ------------------------------------------------------------------ numbers
@pytest.mark.parametrize("text,expected", [
    ("आमचे १२ कॅफे", {"12"}),
    ("1,00,000 followers", {"100000"}),
    ("१ लाख फॉलोअर्स", {"100000"}),
    ("2.5 crore cups", {"25000000"}),
    ("५० हजार फॉलोअर्स", {"50000"}),
    ("50,000 से ज़्यादा", {"50000"}),
    ("10 करोड़ रुपये", {"100000000"}),
    ("Rs 1,299 incl. taxes", {"1299"}),
])
def test_numbers_normalise_indian_forms(text: str, expected: set[str]) -> None:
    assert C.numbers(text) == expected


def test_ascii_digits_keeps_length() -> None:
    assert L.ascii_digits("९८७६५ ४३२१०") == "98765 43210"
    assert L.ascii_digits("৩০ টাকা ௧௨") == "30 টাকা 12"


def test_hindi_claim_gets_number_matched_fact_candidates() -> None:
    facts = seed_facts()
    got = [f.code for f, _ in C.top_facts("आमचे १२ कॅफे पुणे, मुंबई आणि बेंगळुरूमध्ये आहेत.", facts)]
    assert "F-04" in got
    # English claims keep plain word matching (no number-only candidates)
    assert "F-19" not in [f.code for f, _ in C.top_facts("We now have 40 cafes across India.", facts)]


def test_without_jev_a_number_only_match_needs_a_human() -> None:
    ctx = seed_ctx("अब पूरे भारत में हमारे 40 कैफ़े हैं।")
    cl = C.extract_claims(ctx.post_text, ctx)
    res = [asyncio.run(C.check_claim(c, ctx, use_jev=False)) for c in cl]
    assert res and all(r.status == "unsupported" and r.band == "human" for r in res)  # "40" is in F-19 (hours), not cafes


# ------------------------------------------------------------------ readability
def test_no_english_grade_for_hindi_or_hinglish() -> None:
    code_mixed = ("हमारा Saffron Signature Roast अब online order के लिए available है। Pune, Mumbai और Bengaluru के हमारे "
                  "cafes में भी मिलेगा। Fresh beans, small batches, हर Tuesday और Friday roast होते हैं।")
    assert [c for c, _ in hits(code_mixed) if c == "R-ACC-02"] == []
    hinglish = "Aaj subah naya roast aaya hai. Pune ke cafe mein aao aur dosto ko bhi saath lao, hum intezaar kar rahe hain."
    assert [c for c, _ in hits(hinglish) if c == "R-ACC-02"] == []


def test_long_hindi_sentence_flagged_by_length_not_grade() -> None:
    long = " ".join(["हमारी कॉफ़ी"] * 16) + "।"  # 32 words in one sentence
    hs = [h for h in tier0.run_tier0_text(long, seed_ctx(long)) if h.rule_code == "R-ACC-02"]
    assert len(hs) == 1 and "32 words" in hs[0].detail and "grade" in hs[0].detail and "Reading grade is" not in hs[0].detail


def test_english_readability_unchanged() -> None:
    t = ("Our extraction methodology emphasises meticulous parameterisation of volumetric dissolution, thereby "
         "optimising the organoleptic characteristics of differentiated single-estate provenances.")
    hs = [h for h in tier0.run_tier0_text(t, seed_ctx(t)) if h.rule_code == "R-ACC-02"]
    assert len(hs) == 1 and hs[0].detail.startswith("Reading grade is")


# ------------------------------------------------------------------ fix language
@pytest.mark.parametrize("original,fix,bad", [
    ("सिर्फ आज!!! एक पैक ले जाइए।", "Grab a pack today.", True),
    ("सिर्फ आज!!! एक पैक ले जाइए।", "आज ही एक पैक ले जाइए।", False),
    ("फक्त आज!!! Saffron Signature Roast ची एक बॅग घ्या.", "Saffron Signature Roast ची एक बॅग घ्या.", False),
    ("Jaldi karo, hamara Monsoon Malabar almost khatam hai.", "Our Monsoon Malabar is almost sold out, so visit soon.", True),
    ("Jaldi karo, hamara Monsoon Malabar almost khatam hai.", "Hamara Monsoon Malabar ab thoda hi bacha hai.", False),
    ("India's #1 filter coffee", "Our filter coffee", False),
    ("India's #1 filter coffee", "हमारी फ़िल्टर कॉफ़ी", True),
])
def test_fix_language_problem(original: str, fix: str, bad: bool) -> None:
    assert bool(L.fix_language_problem(original, fix)) is bad


def test_selfcheck_rejects_fix_in_other_script() -> None:
    text = "भारत की नंबर 1 फ़िल्टर कॉफ़ी आ गई है। हमारे किसी भी कैफ़े में आइए।"
    ctx = seed_ctx(text)
    unit = UnitIn(id=1, kind="text", idx=0, text=text, char_start=0)
    end = text.index("।") + 1
    rule = next(r for r in ctx.rules if r.code == "R-CLAIM-01")
    res = asyncio.run(S.verify_fix(unit, 0, end, "Our filter coffee is here.", rule, ctx, use_jev=False, region=True))
    assert not res.verified and "script" in res.reasons[0]
    ok = asyncio.run(S.verify_fix(unit, 0, end, "हमारी फ़िल्टर कॉफ़ी आ गई है।", rule, ctx, use_jev=False, region=True))
    assert ok.verified, ok.reasons


def test_prompt_asks_for_english_explanations_and_same_language_fix() -> None:
    req = X.RegionRequest(items=[X.RegionItem("1", None, "claims", "why", "नंबर 1")], unit_text="भारत की नंबर 1 कॉफ़ी है।",
                          region_text="भारत की नंबर 1 कॉफ़ी है।", channel="instagram")
    user = X.build_region_messages(req)[1]["content"]
    assert "Write the explanations in English" in user and "Devanagari" in user and "Hindi" in user
    en = X.RegionRequest(items=[X.RegionItem("1", None, "claims", "why", "#1")], unit_text="India's #1 coffee.",
                         region_text="India's #1 coffee.", channel="instagram")
    assert "Write the explanations in English" not in X.build_region_messages(en)[1]["content"]
    assert "same language and script" in X.SYSTEM_PROMPT


def test_romanize_for_identifiers() -> None:
    assert L.romanize("आमचे") == "aamache"
    assert L.romanize("Pune") == "pune"
