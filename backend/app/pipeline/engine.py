"""The check pipeline without a database. OWNER: pipeline-agent.

`analyze(units, ctx, ...)` runs every unit in parallel:
  tier0 -> one Jev call per unit (rule Nouls + tone + has_claim/claim_type, or image Nouls)
  -> claims (one Jev call per claim) -> bands -> explain (flag/human only) -> self-check
and hands each finished finding to `on_finding` as soon as it is final. run.py persists and
streams them; the eval runner just collects them.

Confidential: Tier 0 only (and number-matched claims); no Jev, no LLM, nothing in the CallLog.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.pipeline import claims as C
from app.pipeline import explain as X
from app.pipeline import jev
from app.pipeline import logo as L
from app.pipeline import questions as Q
from app.pipeline import selfcheck as S
from app.pipeline.bands import band as to_band
from app.pipeline.context import CheckContext, RuleSpec, UnitIn
from app.pipeline.privacy import mask_pii_map, unmask
from app.pipeline.tier0 import Tier0Hit, run_tier0_units, sentence_at, sentences

log = logging.getLogger("brandgate.engine")

IMAGE_UNAVAILABLE = "Image judged by code checks only (Jev image check unavailable)."


@dataclass
class FindingDraft:
    unit_id: Optional[int]
    unit_idx: Optional[int]
    rule_code: Optional[str]
    category: str
    severity: str
    band: str
    tier: str  # tier0|jev|claim|visual
    probability: Optional[float]
    quote: str = ""
    local_start: Optional[int] = None
    local_end: Optional[int] = None
    span_start: Optional[int] = None  # Version.text offsets
    span_end: Optional[int] = None
    fact_code: Optional[str] = None
    claim_status: Optional[str] = None
    rule_version: Optional[int] = None
    explanation: str = ""
    fix: Optional[str] = None
    fix_verified: bool = False
    detail: str = ""  # template explanation detail (code-generated, never model output)
    det_fix: Optional[str] = None  # deterministic fix from Tier 0 (e.g. preferred term)
    problem: str = ""  # plain words for the LLM prompt
    facts_note: list[str] = field(default_factory=list)
    selfcheck_reasons: list[str] = field(default_factory=list)
    box: Optional[dict] = None  # image region as fractions {top, left, bottom, right} (logo findings)
    quad: Optional[list] = None  # projected logo corners [[x, y] fractions] TL, TR, BR, BL
    # Edit regions: findings whose fixes would touch the same sentence share ONE fix. Their span/quote
    # become the whole region (so `fix` still replaces exactly span); hit_* keep the flagged words.
    hit_start: Optional[int] = None  # Version.text offsets of the flagged words
    hit_end: Optional[int] = None
    fix_group: Optional[str] = None  # same value = same region = same fix (applied once)
    local_only: bool = False  # explained by code (web accessibility audit): no LLM explanation or fix


@dataclass
class Judgment:
    """One banded Jev probability (for the rule scan panel, calibration and threshold tuning)."""

    unit_idx: Optional[int]
    rule_code: Optional[str]
    category: str
    p: float
    kind: str  # rule|claim|image|cta
    local_start: Optional[int] = None
    local_end: Optional[int] = None
    claim_status: Optional[str] = None
    fact_code: Optional[str] = None


@dataclass
class EngineResult:
    findings: list[FindingDraft] = field(default_factory=list)
    judgments: list[Judgment] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    tones: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pii_masked: int = 0
    logo: list[dict] = field(default_factory=list)  # P1 logo fidelity per image unit (for the overlay)
    units_total: int = 0
    units_done: int = 0
    elapsed_ms: int = 0
    jev_used: bool = False
    llm_used: bool = False


OnFinding = Callable[[FindingDraft], Awaitable[None]]
OnProgress = Callable[[str, EngineResult], Awaitable[None]]


async def _noop(*_: Any) -> None:
    return None


class _Run:
    def __init__(self, units: list[UnitIn], ctx: CheckContext, calls: jev.CallLog, use_jev: bool, use_llm: bool,
                 explain_findings: bool, on_finding: OnFinding, on_progress: OnProgress) -> None:
        self.units = sorted(units, key=lambda u: u.idx)
        self.ctx = ctx
        self.calls = calls
        self.use_jev = use_jev
        self.use_llm = use_llm
        self.explain_findings = explain_findings
        self.on_finding = on_finding
        self.on_progress = on_progress
        self.res = EngineResult(units_total=len(self.units), jev_used=use_jev, llm_used=use_llm)
        self.rules = ctx.enabled_rules()
        self.masks: dict[int, dict[str, str]] = {}
        self.context_post: str = ctx.post_text

    # ---------------------------------------------------------------- helpers
    def rule(self, code: Optional[str]) -> Optional[RuleSpec]:
        return self.ctx.rule_by_code(code)

    def mask(self, u: UnitIn) -> str:
        masked, mapping = mask_pii_map(u.text)
        self.masks[u.idx] = mapping
        self.res.pii_masked += len(mapping)
        return masked

    def draft(self, u: Optional[UnitIn], rule: Optional[RuleSpec], *, band: str, tier: str, p: Optional[float],
              start: Optional[int], end: Optional[int], category: str = "", severity: str = "minor", **kw: Any) -> FindingDraft:
        given_code = kw.pop("rule_code", None)
        override = kw.pop("severity_override", None)  # code-decided (e.g. P1 logo findings), never a model's
        d = FindingDraft(
            unit_id=u.id if u else None, unit_idx=u.idx if u else None, rule_code=rule.code if rule else given_code,
            category=rule.category if rule else category, severity=override or (rule.severity if rule else severity), band=band,
            tier=tier, probability=p, local_start=start, local_end=end,
            rule_version=rule.version if rule else None, **kw)
        if u is not None and start is not None and end is not None:
            d.quote = u.text[start:end]
            if u.char_start is not None:
                d.span_start, d.span_end = u.char_start + start, u.char_start + end
        return d

    def text_rules(self) -> list[RuleSpec]:
        return [r for r in self.rules if r.check_method == "jev" and r.category != "visual"
                and not any(k in (r.params or {}) for k in Q.CTA_PARAM_KEYS)]

    def image_rules(self) -> list[RuleSpec]:
        return [r for r in self.rules if (r.check_method == "visual" or (r.check_method == "jev" and r.category == "visual"))
                and not any(k in (r.params or {}) for k in Q.IMAGE_RULES_IN_CODE)]

    def cap_band(self, rule: RuleSpec, b: str) -> str:
        """Deliberately vague rules (params.vague, e.g. "Posts should feel premium") create no findings:
        on the dev split Jev rated most on-brand copy high on them (32 false alarms vs 2 hits). Their
        probabilities still appear in the rule scan; rewriting them is Round 3 (unclear-rule detection)."""
        if (rule.params or {}).get("vague"):
            return "pass"
        return b

    @staticmethod
    def is_vague(rule: Optional[RuleSpec]) -> bool:
        return bool(rule and (rule.params or {}).get("vague"))

    # ---------------------------------------------------------------- per unit
    async def text_unit(self, u: UnitIn, hits: list[Tier0Hit]) -> list[FindingDraft]:
        out = [self.from_tier0(u, h) for h in hits]
        tier0_rules = {h.rule_code for h in hits if h.rule_code}
        masked = self.mask(u) if self.use_jev or self.use_llm else u.text
        # Trigger-based claims don't need the rule call's answers, so they run alongside it.
        found = C.extract_claims(u.text, self.ctx, covered=hits)
        claim_tasks = [asyncio.ensure_future(self.check_claim(u, cl)) for cl in found]
        has_claim, claim_type = None, None
        if self.use_jev and len(u.text.split()) >= Q.MIN_WORDS_FOR_JEV:
            qs: dict[str, Any] = {r.code: Q.rule_question(r) for r in self.text_rules() if not Q.uses_tone_choice(r)}
            if self.ctx.tone_labels:
                qs["tone"] = Q.tone_question(self.ctx.tone_labels)
            qs["has_claim"] = Q.HAS_CLAIM
            qs["claim_type"] = Q.CLAIM_TYPE
            res = await jev.ask(Q.text_state(masked, self.ctx.channel), qs, purpose="rules", calls=self.calls, unit_id=u.id)
            if not res.ok:
                self.res.warnings.append(f"Jev rule check failed for paragraph {u.idx + 1} ({res.error}); "
                                         "Tier 0 findings are shown, the Jev rules were not checked.")
            else:
                tone = res.choices.get("tone")
                for r in self.text_rules():
                    if Q.uses_tone_choice(r):
                        if tone is None:
                            continue
                        p = Q.tone_violation(r, tone.probabilities, Q.forbidden_tones(self.text_rules()))
                    else:
                        p = res.nouls[r.code]
                    b = self.cap_band(r, to_band(p, r.category))
                    self.res.judgments.append(Judgment(u.idx, r.code, r.category, p, "rule", 0, len(u.text)))
                    if b != "pass" and r.code not in tier0_rules:
                        problem = (f"Jev reads the tone as “{tone.choice}”; the rule allows {', '.join(Q.allowed_tones(r))}."
                                   if Q.uses_tone_choice(r) and tone else
                                   f"Jev estimates a {p:.0%} chance that this paragraph breaks {r.code}.")
                        out.append(self.draft(u, r, band=b, tier="jev", p=round(p, 4), start=0, end=len(u.text), problem=problem))
                if "tone" in res.choices:
                    t = res.choices["tone"]
                    self.res.tones.append({"unit_idx": u.idx, "tone": t.choice,
                                           "p": round(t.probabilities.get(t.choice, t.confidence or 0), 3)})
                has_claim = res.nouls.get("has_claim")
                if has_claim is not None and has_claim > Q.HAS_CLAIM_AT and "claim_type" in res.choices:
                    claim_type = res.choices["claim_type"].choice
        await self.on_progress("jev", self.res)
        if not found and not hits and claim_type in ("objective", "comparative"):
            claim_tasks.append(asyncio.ensure_future(self.check_claim(u, C.unit_claim(u.text, self.ctx, claim_type))))
        for cr in await asyncio.gather(*claim_tasks):
            if cr.is_finding:
                out.append(self.from_claim(u, cr))
        await self.on_progress("claims", self.res)
        return out

    async def check_claim(self, u: UnitIn, cl: C.Claim) -> C.ClaimResult:
        cr = await C.check_claim(cl, self.ctx, use_jev=self.use_jev, calls=self.calls, unit_id=u.id,
                                 masked_text=mask_pii_map(cl.text)[0])
        self.res.claims.append({"unit_idx": u.idx, "group": cr.claim.group, "status": cr.status, "via": cr.via,
                                "rule_code": cr.rule_code, "fact_code": cr.fact_code, "p": cr.probability,
                                "local_start": cr.claim.start, "local_end": cr.claim.end, "candidates": cr.candidates})
        if cr.probability is not None and cr.via in ("jev", "numbers"):
            self.res.judgments.append(Judgment(u.idx, cr.rule_code, "claims", cr.probability, "claim",
                                               cr.claim.start, cr.claim.end, cr.status, cr.fact_code))
        if cr.error:
            self.res.warnings.append(f"Jev claim check failed in paragraph {u.idx + 1} ({cr.error}); the claim needs a human look.")
        return cr

    def logo_rule(self) -> Optional[RuleSpec]:
        return next((r for r in self.rules if "logo_file" in (r.params or {})), None)

    async def image_unit(self, u: UnitIn, hits: list[Tier0Hit]) -> list[FindingDraft]:
        out = [self.from_tier0(u, h) for h in hits]
        logo_rule = self.logo_rule() if (self.ctx.logo_path and u.image_path) else None
        # Logo fidelity is local (OpenCV): it runs for confidential items too, alongside the Jev call.
        logo_task = (asyncio.ensure_future(asyncio.to_thread(L.match_logo, u.image_path, self.ctx.logo_path,
                                                             palette=self.ctx.palette, colour_threshold=self.palette_threshold()))
                     if logo_rule else None)
        rules = self.image_rules()
        res: Optional[jev.JevResult] = None
        if self.use_jev and (rules or logo_rule) and u.image_path:
            qs: dict[str, Any] = {}
            pre: dict[str, str] = {}
            for r in rules:
                qs[r.code] = Q.image_rule_question(r)
                pc = Q.image_precondition(r)
                if pc:
                    qs[pc[0]] = pc[1]
                    pre[r.code] = pc[0]
            if logo_rule:
                qs["has_logo"] = Q.HAS_LOGO
            try:
                from typesafe_sdk import Image

                state: Any = {"image": Image(jev.prepare_image(u.image_path))}
                res = await jev.ask(state, qs, purpose="rules_image", calls=self.calls, unit_id=u.id, images=True)
            except Exception as exc:  # noqa: BLE001  (image prep or SDK asset errors)
                res = jev.JevResult(ok=False, error=type(exc).__name__)
            if not res.ok:
                self.res.warnings.append(f"Jev image check failed ({res.error}).")
                out.append(self.draft(u, None, band="human", tier="visual", p=None, start=None, end=None, category="visual",
                                      severity="minor", explanation=IMAGE_UNAVAILABLE, detail=IMAGE_UNAVAILABLE))
            else:
                tier0_rules = {h.rule_code for h in hits}
                for r in rules:
                    p = res.nouls[r.code]
                    if r.code in pre:
                        gate = res.nouls[pre[r.code]]
                        if gate < Q.HUMAN_AT:
                            p = 0.0  # nothing to judge (e.g. no text in the image): pass
                        elif gate < Q.PRECONDITION_AT:
                            p = min(p, Q.FLAG_AT - 0.01)  # unsure whether it applies: at most a human look
                    b = self.cap_band(r, to_band(p, r.category))
                    self.res.judgments.append(Judgment(u.idx, r.code, r.category, p, "image"))
                    if b != "pass" and r.code not in tier0_rules:
                        out.append(self.draft(u, r, band=b, tier="visual", p=round(p, 4), start=None, end=None,
                                              problem=f"Jev estimates a {p:.0%} chance that the image breaks {r.code}."))
        if logo_task is not None and logo_rule is not None:
            m: L.LogoMatch = await logo_task
            has_logo = res.nouls.get("has_logo") if (res is not None and res.ok) else None
            out += self.logo_findings(u, logo_rule, m, has_logo)
        return out

    def palette_threshold(self) -> float:
        r = next((r for r in self.rules if "palette_delta_e" in (r.params or {})), None)
        try:
            return float(r.params["palette_delta_e"]) if r else L.DEFAULT_COLOUR_DELTA_E
        except (TypeError, ValueError):
            return L.DEFAULT_COLOUR_DELTA_E

    def logo_findings(self, u: UnitIn, rule: RuleSpec, m: L.LogoMatch, has_logo: Optional[float]) -> list[FindingDraft]:
        """P1-2: code turns the SIFT measurement (+ Jev's has_logo, when asked) into findings."""
        if m.error:
            self.res.warnings.append(f"Logo check could not run ({m.error}).")
            self.res.logo.append({"unit_idx": u.idx, "verdict": None, "error": m.error})
            return []
        v = L.decide(m, has_logo)
        self.res.logo.append({"unit_idx": u.idx, "verdict": v.verdict, "band": v.band, "reason": v.reason,
                              "inliers": m.inliers, "raw_inliers": m.raw_inliers, "good_matches": m.good_matches,
                              "has_logo": None if has_logo is None else round(has_logo, 4), "stretched": v.stretched,
                              "box": m.box, "quad": m.quad, "aspect_deviation": m.aspect_deviation,
                              "colours": m.colours, "off_palette": m.off_palette, "elapsed_ms": m.elapsed_ms})
        out: list[FindingDraft] = []
        cite = X.template_explanation(rule)
        if v.verdict == "altered":
            title = "Logo looks stretched." if v.stretched else "Logo looks redrawn or altered."
            out.append(self.draft(u, rule, band=v.band, tier="logo", p=None, start=None, end=None, severity_override="major",
                                  detail=v.reason, explanation=f"{title} {v.reason} {cite}", box=m.box, quad=m.quad))
        elif v.verdict == "not_found" and v.band != "pass" and (rule.params or {}).get("logo_required"):
            out.append(self.draft(u, rule, band=v.band, tier="logo", p=None, start=None, end=None, severity_override="minor",
                                  detail=v.reason, explanation=f"No approved logo found. {v.reason} {cite}"))
        if v.verdict == "genuine" and m.off_palette:
            desc = "; ".join(f"{c['hex']} ({c['share']:.0%} of the logo, ΔE {c['delta_e']:g} from {c['nearest']})"
                             for c in m.off_palette)
            detail = f"Off-palette colours inside the logo: {desc}."
            out.append(self.draft(u, rule, band="flag", tier="logo", p=None, start=None, end=None, severity_override="minor",
                                  detail=detail, explanation=f"Logo colour off-palette. {detail} {cite}",
                                  box=m.box, quad=m.quad))
        return out

    async def cta_post(self) -> list[FindingDraft]:
        """Post-level call-to-action count: Jev judges each sentence, code counts."""
        rule = next((r for r in self.rules if r.check_method == "jev" and any(k in (r.params or {}) for k in Q.CTA_PARAM_KEYS)), None)
        text_units = [u for u in self.units if u.is_text and u.text.strip()]
        if rule is None or not self.use_jev or not text_units:
            return []
        sents: list[tuple[UnitIn, int, int]] = [(u, s, e) for u in text_units for s, e in sentences(u.text)]
        if not sents or len(sents) > 40:
            return []
        post = "\n\n".join(mask_pii_map(u.text)[0] for u in text_units)
        qs: dict[str, Any] = {}
        for i, (u, s, e) in enumerate(sents):
            sent = mask_pii_map(u.text[s:e])[0]
            qs[f"cta_{i}"] = Q.cta_question(sent)
            qs[f"multi_{i}"] = Q.multi_cta_question(sent)
        res = await jev.ask(Q.text_state(post, self.ctx.channel), qs, purpose="rules_post", calls=self.calls)
        if not res.ok:
            self.res.warnings.append(f"Jev call-to-action check failed ({res.error}).")
            return []
        p_cta = [res.nouls[f"cta_{i}"] for i in range(len(sents))]
        p_multi = [res.nouls[f"multi_{i}"] for i in range(len(sents))]
        counted = sorted([p for p in p_cta if p > Q.CTA_AT] + [p for p in p_multi if p > Q.CTA_AT], reverse=True)
        lo = int(rule.params.get("min_ctas", 0) or 0)
        hi = rule.params.get("max_ctas")
        if len(counted) < lo:
            # A missing call to action is recorded for the rule scan only: on the dev split it was
            # never labelled as a problem (20 false alarms), while "too many" was.
            self.res.judgments.append(Judgment(None, rule.code, rule.category, 1 - max(p_cta), "cta"))
            return []
        if hi is not None and len(counted) > int(hi):
            p = counted[int(hi)]
            self.res.judgments.append(Judgment(None, rule.code, rule.category, p, "cta"))
            idx = [i for i in range(len(sents)) if p_cta[i] > Q.CTA_AT or p_multi[i] > Q.CTA_AT]
            first, last = sents[idx[0]], sents[idx[-1]]
            u = first[0] if first[0] is last[0] else None
            d = self.draft(u, rule, band=to_band(p, rule.category), tier="jev", p=round(p, 4),
                           start=first[1] if u else None, end=last[2] if u else None,
                           detail=f"The post asks the reader to do {len(counted)} different things.",
                           problem=f"The post asks the reader to do {len(counted)} different things; keep one call to action.")
            if u is None and first[0].char_start is not None and last[0].char_start is not None:
                d.span_start, d.span_end = first[0].char_start + first[1], last[0].char_start + last[2]
            return [d] if d.band != "pass" else []
        self.res.judgments.append(Judgment(None, rule.code, rule.category, 0.0, "cta"))
        return []

    # ---------------------------------------------------------------- finding builders
    def from_tier0(self, u: UnitIn, h: Tier0Hit) -> FindingDraft:
        rule = self.rule(h.rule_code)
        return self.draft(u, rule, band=h.band, tier="tier0", p=h.probability, start=h.span_start, end=h.span_end,
                          rule_code=h.rule_code, category=h.category, severity=h.severity, detail=h.detail, det_fix=h.fix,
                          problem=h.detail or "A code check matched this text.")

    def from_claim(self, u: UnitIn, cr: C.ClaimResult) -> FindingDraft:
        rule = self.rule(cr.rule_code)
        fact = next((f for f in self.ctx.facts if f.code == cr.fact_code), None)
        current = [f for f in self.ctx.facts if any(c["code"] == f.code and not c["expired"] for c in cr.candidates)]
        notes = [f"{f.code}: {f.statement} (current)" for f in current]
        if cr.status == "outdated" and fact is not None:
            detail = f"It relies on fact {fact.code} (“{fact.statement}”), which expired on {fact.valid_until:%d %b %Y}."
            notes.insert(0, f"{fact.code}: {fact.statement} (EXPIRED on {fact.valid_until}; don't use it)")
        elif cr.via == "no_jev":
            detail = "No approved fact could be confirmed for this claim (unsupported, no Jev check)."
        else:
            detail = "No current approved fact supports this claim."
        tier = "claim"
        d = self.draft(u, rule, band=cr.band, tier=tier, p=cr.probability, start=cr.claim.start, end=cr.claim.end,
                       fact_code=cr.fact_code, claim_status=cr.status, detail=detail, problem=detail, facts_note=notes,
                       category=rule.category if rule else "claims", severity=rule.severity if rule else "major")
        return d

    # ---------------------------------------------------------------- explain + self-check
    async def finalize(self, d: FindingDraft) -> FindingDraft:
        rule = self.rule(d.rule_code)
        if not d.explanation:
            d.explanation = X.template_explanation(rule, d.detail)
        if d.tier == "logo" or d.local_only:  # measured locally; the LLM can't see the image and has nothing to add
            return d
        if d.band == "pass" or not self.explain_findings or self.is_vague(rule):
            d.fix = d.det_fix
            return d
        u = next((x for x in self.units if x.idx == d.unit_idx), None)
        fixable = u is not None and u.is_text and d.local_start is not None and d.local_end is not None
        fix = d.det_fix if fixable else None
        req = None
        if self.use_llm and u is not None and (u.is_text or u.kind == "image") and d.explanation != IMAGE_UNAVAILABLE:
            mapping = self.masks.get(u.idx) or mask_pii_map(u.text)[1]
            masked_unit = mask_pii_map(u.text)[0] if u.is_text else "(an image)"
            masked_quote = mask_pii_map(d.quote)[0] if d.quote else ""
            whole = fixable and d.local_start == 0 and d.local_end == len(u.text)
            req = X.ExplainRequest(rule=rule, category=d.category, problem=d.problem or d.detail, unit_text=masked_unit,
                                   quote=masked_quote, channel=self.ctx.channel, whole_unit=bool(whole), facts=d.facts_note)
            out = await X.explain(req, calls=self.calls, unit_id=d.unit_id)
            if out is not None:
                d.explanation = out.explanation
                if fixable and fix is None and out.fix:
                    fix = unmask(out.fix, mapping)
        if not fixable or not fix:
            d.fix, d.fix_verified = fix if fixable else None, False
            return d
        assert u is not None and d.local_start is not None and d.local_end is not None
        chk = await S.verify_fix(u, d.local_start, d.local_end, fix, rule, self.ctx, use_jev=self.use_jev, calls=self.calls,
                                 deterministic=fix == d.det_fix)
        if not chk.verified and req is not None:
            out = await X.explain(req, calls=self.calls, unit_id=d.unit_id, feedback="; ".join(chk.reasons),
                                  previous_fix=mask_pii_map(fix)[0], purpose="explain_retry")
            if out is not None and out.fix:
                fix2 = unmask(out.fix, self.masks.get(u.idx) or {})
                chk2 = await S.verify_fix(u, d.local_start, d.local_end, fix2, rule, self.ctx, use_jev=self.use_jev, calls=self.calls)
                if chk2.verified or not chk.verified:
                    fix, chk = fix2, chk2
        d.fix, d.fix_verified, d.selfcheck_reasons = fix, chk.verified, chk.reasons
        return d

    async def emit(self, d: FindingDraft) -> None:
        d = await self.finalize(d)
        self.res.findings.append(d)
        await self.on_finding(d)

    # ---------------------------------------------------------------- edit regions
    def regionable(self, u: UnitIn, d: FindingDraft) -> bool:
        return (u.is_text and d.unit_idx == u.idx and d.local_start is not None and d.local_end is not None
                and d.band != "pass" and d.tier != "logo" and not self.is_vague(self.rule(d.rule_code)))

    def plan_regions(self, u: UnitIn, drafts: list[FindingDraft]) -> list[tuple[Optional[tuple[int, int]], list[FindingDraft]]]:
        """Group findings into edit regions: each finding's span widened to whole sentences, overlapping
        regions merged. A region with one finding that has a word-list fix keeps its small span."""
        if not (self.use_llm and self.explain_findings and u.is_text):
            return [(None, [d]) for d in drafts]
        out: list[tuple[Optional[tuple[int, int]], list[FindingDraft]]] = []
        regs: list[tuple[int, int, FindingDraft]] = []
        for d in drafts:
            if self.regionable(u, d):
                assert d.local_start is not None and d.local_end is not None
                regs.append((*sentence_span(u.text, d.local_start, d.local_end), d))
            else:
                out.append((None, [d]))
        groups = edit_regions([(s, e) for s, e, _ in regs], u.text)
        for (gs, ge), idx in groups:
            members = [regs[i][2] for i in idx]
            if len(members) == 1 and members[0].det_fix is not None:
                out.append((None, members))
            else:
                out.append(((gs, ge), members))
        return out

    async def emit_region(self, u: UnitIn, span: tuple[int, int], members: list[FindingDraft]) -> None:
        for d in await self.finalize_region(u, span, members):
            self.res.findings.append(d)
            await self.on_finding(d)

    async def finalize_region(self, u: UnitIn, span: tuple[int, int], members: list[FindingDraft]) -> list[FindingDraft]:
        """ONE fix for every finding in an edit region: word-list replacements are applied in code first,
        then one LLM call (listing every rule) rewrites the whole sentence(s), and the result is
        self-checked against all those rules plus the blocking rules. Rejected twice -> no fix
        ("manual fix needed"), unless the fix is purely word-list (safe by construction)."""
        rs, re_ = span
        members = sorted(members, key=lambda d: (d.local_start or 0, d.local_end or 0, d.rule_code or ""))
        rules = [r for r in (self.rule(d.rule_code) for d in members) if r is not None]
        # 1. word-list fixes, in code (earliest wins on overlap, e.g. "!!!" -> "." vs "!")
        base, pos, required, all_det = "", rs, [], all(d.det_fix is not None for d in members)
        for d in members:
            assert d.local_start is not None and d.local_end is not None
            if d.det_fix is None or d.local_start < pos:
                continue
            base += u.text[pos:d.local_start] + d.det_fix
            pos = d.local_end
            if any(ch.isalpha() for ch in d.det_fix):
                required.append(d.det_fix)
        base += u.text[pos:re_]
        # 2. one LLM call for the region
        mapping = self.masks.get(u.idx) or mask_pii_map(u.text)[1]
        # A claim on an EXPIRED fact (e.g. an ended offer) must be removed: only the expired note is passed,
        # since its "current" candidates are other facts the LLM would otherwise swap in (another offer, price).
        removal = any(d.claim_status == "outdated" for d in members)
        notes: list[str] = []
        for d in members:
            ns = [n for n in d.facts_note if "EXPIRED" in n] if d.claim_status == "outdated" else d.facts_note
            notes += [n for n in ns if n not in notes]
        req = X.RegionRequest(
            items=[X.RegionItem(str(i + 1), self.rule(d.rule_code), d.category, d.problem or d.detail,
                                mask_pii_map(d.quote)[0]) for i, d in enumerate(members)],
            unit_text=mask_pii_map(self.context_post or u.text)[0], region_text=mask_pii_map(base)[0], channel=self.ctx.channel,
            required=required, facts=notes)
        out = await X.explain_region(req, calls=self.calls, unit_id=u.id)
        for i, d in enumerate(members):
            e = (out.explanations.get(str(i + 1)) or out.explanations.get("*")) if out else None
            d.explanation = e or d.explanation or X.template_explanation(self.rule(d.rule_code), d.detail)
        fix: Optional[str] = base if all_det else (unmask(out.fix, mapping) if out and out.fix else None)
        chk: Optional[S.SelfCheckResult] = None
        if fix is not None:
            chk = await self.check_region(u, rs, re_, fix, rules, all_det, base, removal)
        if not all_det and (chk is None or not chk.verified):
            fb = "; ".join(chk.reasons) if chk else "no fix was returned"
            out2 = await X.explain_region(req, calls=self.calls, unit_id=u.id, feedback=fb,
                                          previous_fix=mask_pii_map(fix or "")[0], purpose="explain_retry")
            if out2 is not None and out2.fix:
                fix2 = unmask(out2.fix, mapping)
                chk2 = await self.check_region(u, rs, re_, fix2, rules, False, base, removal)
                if chk2.verified or chk is None:
                    fix, chk = fix2, chk2
        verified = bool(chk and chk.verified)
        if not verified and not all_det and chk is not None and not only_unavailable(chk.reasons):
            fix = None  # rejected twice: "manual fix needed" (the UI shows it when fix is null)
        group = f"u{u.idx}:{rs}-{re_}"
        for d in members:
            if u.char_start is not None and d.local_start is not None and d.local_end is not None:
                d.hit_start, d.hit_end = u.char_start + d.local_start, u.char_start + d.local_end
                d.span_start, d.span_end = u.char_start + rs, u.char_start + re_
            d.local_start, d.local_end, d.quote = rs, re_, u.text[rs:re_]
            d.fix, d.fix_verified, d.fix_group = fix, verified, group
            d.selfcheck_reasons = chk.reasons if chk else ["no fix was returned"]
        return members

    async def check_region(self, u: UnitIn, rs: int, re_: int, fix: str, rules: list[RuleSpec], det: bool,
                           base: str, removal: bool = False) -> S.SelfCheckResult:
        return await S.verify_fix(u, rs, re_, fix, rules[0] if rules else None, self.ctx, use_jev=self.use_jev,
                                  calls=self.calls, deterministic=det, extra_rules=rules[1:], region=True,
                                  meaning_original=u.text[:rs] + base + u.text[re_:], removal_required=removal)

    async def unit_task(self, u: UnitIn, hits: list[Tier0Hit]) -> None:
        hits = merge_by_sentence(u.text, hits) if u.is_text else hits
        if u.kind == "image":
            drafts = await self.image_unit(u, hits)
        elif u.is_text:
            drafts = await self.text_unit(u, hits)
        else:
            drafts = [self.from_tier0(u, h) for h in hits]
        await self.on_progress("explain", self.res)
        await asyncio.gather(*[self.emit(g[0]) if span is None else self.emit_region(u, span, g)
                               for span, g in self.plan_regions(u, drafts)])
        self.res.units_done += 1
        await self.on_progress("selfcheck", self.res)

    async def post_task(self) -> None:
        drafts = await self.cta_post()
        await asyncio.gather(*[self.emit(d) for d in drafts])

    async def web_task(self) -> None:
        """Web pages: the accessibility issues found in the HTML at fetch time become findings (code only)."""
        if not self.ctx.web_issues:
            return
        from app.pipeline.webpage import finding_specs

        for sp in finding_specs(self.ctx.web_issues, self.rules):
            u = next((x for x in self.units if x.idx == sp["unit_idx"]), None) if sp["unit_idx"] is not None else None
            has_span = u is not None and u.is_text and sp["local_start"] is not None and sp["local_end"] is not None
            rule = sp["brand_rule"]
            d = self.draft(u, rule, band=sp["band"], tier="tier0", p=None, start=sp["local_start"] if has_span else None,
                           end=sp["local_end"] if has_span else None, rule_code=sp["rule_code"], category="accessibility",
                           severity=sp["severity"], detail=sp["detail"], problem=sp["detail"], local_only=True,
                           explanation=sp["explanation"] or X.template_explanation(rule, sp["detail"]))
            if not has_span and sp["quote"]:
                d.quote = sp["quote"]
            await self.emit(d)

    async def run(self) -> EngineResult:
        t0 = time.perf_counter()
        tier0 = run_tier0_units(self.units, self.ctx)
        self.context_post = renamed_post(self.ctx.post_text, self.units, tier0)
        await self.on_progress("tier0", self.res)
        await asyncio.gather(*[self.unit_task(u, tier0.get(u.idx, [])) for u in self.units], self.post_task(),
                             self.web_task())
        self.res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return self.res


def sentence_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen [start, end) to the whole sentence(s) it touches."""
    s0 = sentence_at(text, start)[0]
    e0 = sentence_at(text, max(start, end - 1))[1]
    return min(s0, start), max(e0, end)


def edit_regions(spans: list[tuple[int, int]], text: Optional[str] = None) -> list[tuple[tuple[int, int], list[int]]]:
    """Merge overlapping spans, and adjacent ones (only spaces between them in `text`, e.g. "Cheap?" and
    "No way, it is a guaranteed miracle.") -> [((start, end), [indexes into spans])], in text order."""
    order = sorted(range(len(spans)), key=lambda i: spans[i])
    out: list[tuple[tuple[int, int], list[int]]] = []
    for i in order:
        s, e = spans[i]
        touching = bool(out) and text is not None and out[-1][0][1] <= s and text[out[-1][0][1]:s].strip(" \t") == ""
        if out and (s < out[-1][0][1] or touching):
            (gs, ge), idx = out[-1]
            out[-1] = ((gs, max(ge, e)), idx + [i])
        else:
            out.append(((s, e), [i]))
    return out


def renamed_post(post: str, units: list[UnitIn], tier0: dict[int, list[Tier0Hit]]) -> str:
    """The post with every word-list rename applied ("Steam Classic" -> "Saffron Signature Roast"), used as the
    LLM's context so a region rewrite doesn't copy an old name from a sentence it isn't rewriting."""
    if not post:
        return post
    edits: list[tuple[int, int, str]] = []
    for u in units:
        if u.char_start is None or u.text != post[u.char_start:u.char_start + len(u.text)]:
            continue
        for h in tier0.get(u.idx, []):
            if h.fix and h.span_start is not None and h.span_end is not None and any(ch.isalpha() for ch in h.fix):
                edits.append((u.char_start + h.span_start, u.char_start + h.span_end, h.fix))
    out, pos = "", 0
    for s, e, fix in sorted(edits):
        if s < pos:
            continue
        out += post[pos:s] + fix
        pos = e
    return out + post[pos:]


def only_unavailable(reasons: list[str]) -> bool:
    """True when the self-check failed only because Jev could not be reached (an error, not a "no")."""
    return bool(reasons) and all("couldn't run" in r for r in reasons)


def merge_by_sentence(text: str, hits: list[Tier0Hit]) -> list[Tier0Hit]:
    """One finding per rule per sentence for hits without a word-list fix ("Only today, hurry, last
    chance" -> one span), so the LLM writes one coherent fix instead of three that clash."""
    out: list[Tier0Hit] = []
    groups: dict[tuple[str, int], list[Tier0Hit]] = {}
    for h in hits:
        if h.rule_code and h.fix is None and h.span_start is not None and h.kind not in ("readability",):
            groups.setdefault((h.rule_code, sentence_at(text, h.span_start)[0]), []).append(h)
        else:
            out.append(h)
    for hs in groups.values():
        first = min(hs, key=lambda h: h.span_start or 0)
        if len(hs) > 1:
            start = min(h.span_start or 0 for h in hs)
            end = max(h.span_end or 0 for h in hs)
            merged = Tier0Hit(first.rule_code, first.category, first.severity, start, end, text[start:end],
                              detail=first.detail, kind=first.kind)
            out.append(merged)
        else:
            out.append(first)
    return sorted(out, key=lambda h: (h.span_start if h.span_start is not None else -1, h.rule_code or ""))


async def analyze(
    units: list[UnitIn],
    ctx: CheckContext,
    *,
    calls: Optional[jev.CallLog] = None,
    use_jev: Optional[bool] = None,
    use_llm: Optional[bool] = None,
    explain_findings: bool = True,
    on_finding: Optional[OnFinding] = None,
    on_progress: Optional[OnProgress] = None,
) -> EngineResult:
    """Run the pipeline on units. External services are off for confidential items, always."""
    external = not ctx.confidential
    use_jev = external and (jev.available() if use_jev is None else use_jev)
    use_llm = external and (X.available() if use_llm is None else use_llm)
    run = _Run(units, ctx, calls if calls is not None else jev.CallLog(version_id=ctx.version_id), use_jev, use_llm,
               explain_findings, on_finding or _noop, on_progress or _noop)
    return await run.run()
