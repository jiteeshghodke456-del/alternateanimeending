"""ALL Jev calls go through here. OWNER: pipeline-agent.

typesafe-sdk 0.7.x (verified in the sandbox):
    from typesafe_sdk import AsyncTypeSafeClient, Noul, Choice, Score, Box, Image
    async with AsyncTypeSafeClient() as client:            # reads TYPESAFE_API_KEY
        res = await client.system_one(state=..., questions={name: Noul(...)}, model=None)
    res.nouls[name].noul -> float in [0,1];  res.choices[name].choice/.confidence/.probabilities
    res.scores[name].score;  res.boxes[name].box (needs model="jev-preview");  res.usage.input_tokens
    Images: Image(bytes_or_path) as state, or `with client.attach_assets(img): state=f"... {img}"`.
    Errors: typesafe_sdk.TypeSafeError and subclasses (RateLimit, Timeout, Authentication...).

Intended API:

    def is_configured() -> bool                       # get_settings().jev_configured
    def rule_question(rule: Rule) -> Noul             # BUILD.md §6 rule_q; high = problem
    def tone_question(labels: list[str]) -> Choice
    CLAIM_QUESTION: Choice                            # objective|comparative|puffery|none
    def support_question(fact: Fact) -> Noul          # NOTE: high = NOT supported? pick one and document it
    MEANING_QUESTION: Noul                            # self-check: keeps message + CTA

    @dataclass
    class JevResult:
        nouls: dict[str, float]; choices: dict[str, tuple[str, float, dict[str, float]]]
        input_tokens: int; latency_ms: int; ok: bool; error: str | None

    async def ask(state, questions: dict[str, Question], *, purpose: str, version_id: int | None,
                  unit_id: int | None, model: str | None = None) -> JevResult
        One call, guarded by a module-level asyncio.Semaphore(8). Logs one AICall row
        (provider="jev", model, purpose, unit_id, input_tokens, latency_ms,
        cost_usd = tokens * JEV_USD_PER_M_INPUT / 1e6). Never logs content. Never raises:
        returns ok=False on error so the pipeline can fall back to Tier 0.

    def band(p: float, category: str | None = None) -> Literal["flag", "human", "pass"]
        >= 0.70 flag, 0.30-0.70 human, < 0.30 pass (per-category override from config).

Callers must pass text that went through privacy.mask_pii() and must not call this for
confidential items.
"""
from __future__ import annotations

FLAG_AT = 0.70
HUMAN_AT = 0.30


def is_configured() -> bool:
    from app.config import get_settings

    return get_settings().jev_configured


def band(p: float, category: str | None = None) -> str:
    """Default bands from CLAUDE.md. Per-category tuning (dev split only) is pipeline-agent's."""
    if p >= FLAG_AT:
        return "flag"
    if p >= HUMAN_AT:
        return "human"
    return "pass"


async def ask(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    raise NotImplementedError("pipeline-agent: implement jev.ask")
