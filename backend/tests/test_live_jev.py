"""Live smoke tests against the real APIs. Skipped unless RUN_LIVE=1 and keys are present.

    RUN_LIVE=1 pytest -q -m live -s

Uses only fictional demo-brand text and a seed image. Prints latency and tokens. Keys come from the
environment or the repo .env (the unit-test conftest blanks them in os.environ on purpose).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from app.pipeline import jev
from app.pipeline import questions as Q
from pipeline_helpers import SEED, seed_facts, seed_rules

pytestmark = pytest.mark.live

REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


def _env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if REPO_ENV.is_file():
        from dotenv import dotenv_values

        return (dotenv_values(REPO_ENV).get(name) or "").strip()
    return ""


LIVE = os.environ.get("RUN_LIVE") == "1"
JEV_KEY = _env("TYPESAFE_API_KEY") if LIVE else ""
needs_jev = pytest.mark.skipif(not (LIVE and JEV_KEY), reason="set RUN_LIVE=1 and TYPESAFE_API_KEY")


def _ask(state, questions, images: bool = False):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        client = jev._make_client(api_key=JEV_KEY)
        t0 = time.perf_counter()
        res = await client.system_one(state=state, questions=questions,
                                      extra_headers=Q.IMAGE_HEADERS if images else None, timeout=jev.TIMEOUT_S)
        return res, int((time.perf_counter() - t0) * 1000)

    res, ms = asyncio.run(go())
    print(f"\n  model={res.model} latency={ms}ms input_tokens={res.usage.input_tokens}")
    return res


@needs_jev
def test_live_rule_noul_and_support() -> None:
    rule = next(r for r in seed_rules() if r.code == "R-TONE-02")
    fact = next(f for f in seed_facts() if f.code == "F-12")
    res = _ask(Q.text_state("Order now. You would be silly to skip this.", "instagram"),
               {"pushy": Q.rule_question(rule), "has_claim": Q.HAS_CLAIM})
    print(f"  R-TONE-02 noul={res.nouls['pushy'].noul:.2f} has_claim={res.nouls['has_claim'].noul:.2f}")
    assert res.nouls["pushy"].noul > 0.7
    sup = _ask(Q.claim_state("Flat 20% off with code MONSOON20."), {Q.support_key("F-12"): Q.support_question(fact)})
    print(f"  support F-12={sup.nouls['support_F-12'].noul:.2f}")
    assert sup.nouls["support_F-12"].noul > 0.7


@needs_jev
def test_live_tone_choice() -> None:
    res = _ask(Q.text_state("Oh sure, instant coffee is totally a personality.", "instagram"),
               {"tone": Q.tone_question(["warm", "confident", "playful", "formal", "pushy", "sarcastic"])})
    c = res.choices["tone"]
    print(f"  tone={c.choice} p={c.probabilities.get(c.choice):.2f}")
    assert c.choice == "sarcastic"


@needs_jev
def test_live_image_noul() -> None:
    from typesafe_sdk import Image

    rule = next(r for r in seed_rules() if r.code == "R-ACC-03")
    data = jev.prepare_image(str(SEED / "content" / "ad_good.png"))
    res = _ask({"image": Image(data)}, {"R-ACC-03": Q.image_rule_question(rule), "has_text": Q.IMAGE_PRECONDITIONS["min_contrast"][1]},
               images=True)
    print(f"  has_text={res.nouls['has_text'].noul:.2f} low_contrast={res.nouls['R-ACC-03'].noul:.2f}")
    assert 0.0 <= res.nouls["R-ACC-03"].noul <= 1.0


LLM_KEY = _env("LLM_API_KEY") if LIVE else ""


@pytest.mark.skipif(not (LIVE and LLM_KEY), reason="set RUN_LIVE=1 and LLM_API_KEY")
def test_live_llm_explain_json() -> None:
    from openai import AsyncOpenAI

    from app.pipeline import explain as X

    rule = next(r for r in seed_rules() if r.code == "R-CLAIM-01")
    req = X.ExplainRequest(rule=rule, category="claims", problem="No current approved fact supports this claim.",
                           unit_text="Steam Classic is India's #1 coffee, hands down!!!", quote="#1", channel="instagram")

    async def go():  # noqa: ANN202
        client = AsyncOpenAI(base_url=_env("LLM_BASE_URL") or "https://api.deepseek.com", api_key=LLM_KEY, timeout=30)
        t0 = time.perf_counter()
        r = await client.chat.completions.create(model=_env("LLM_MODEL") or "deepseek-chat", messages=X.build_messages(req),
                                                 response_format={"type": "json_object"}, temperature=0.2, max_tokens=400)
        return r, int((time.perf_counter() - t0) * 1000)

    r, ms = asyncio.run(go())
    out = X._parse(r.choices[0].message.content or "")
    print(f"\n  llm latency={ms}ms tokens={r.usage.prompt_tokens}/{r.usage.completion_tokens} fix={out.fix!r}")
    assert "R-CLAIM-01" in out.explanation and len(out.explanation) <= 280
