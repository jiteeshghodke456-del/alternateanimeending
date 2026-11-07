"""Rule packs: ready-made rule sets with cited sources (seed/packs/*.json), e.g. the India regulatory pack.

A pack rule is an ordinary Rule row with `pack_id` set. Its `source_quote` is the citation (document,
clause, a short quoted phrase) and `params.citation` keeps the structured source (URL, retrieved date),
so every finding it raises shows where the rule comes from.

Applying is idempotent: rules are matched by code, so a re-apply updates them and never duplicates.
Removing deletes the pack's rules only.

Overlap with the brand's own rules ("link, don't duplicate"): when a deterministic brand rule (tier0 or
claim) already flags the same words as a pack rule on the pack rule's own violating examples, the pack
rule is linked to it (`params.linked_rules`). Then:
- Tier 0 drops the pack rule's hit in any sentence where a linked brand rule already fired, so the
  words are flagged once (pipeline/tier0.py);
- that brand rule's findings list the regulation under `regulations` (services.to_finding_out), so the
  card shows the brand rule and the legal citation together.
The pack rule still catches what the brand rule misses (e.g. Hindi urgency phrases).
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import Session, select

from app.models import Rule, utcnow
from app.pipeline.context import CheckContext, RuleSpec
from app.pipeline.tier0 import PostState, check_rule_on_text

PACKS_DIR = Path(__file__).parent / "seed" / "packs"
_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class PackSource(BaseModel):
    id: str
    title: str
    short: str
    issuer: str
    year: int
    status: str
    url: str
    retrieved: date

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("source url must be https")
        return v


class PackCite(BaseModel):
    source: str
    clause: str = Field(min_length=3)
    quote: str = Field(default="", max_length=160)  # a short phrase only, never a long extract


class PackRule(BaseModel):
    code: str = Field(pattern=r"^[A-Z]{2,4}-[A-Z0-9]{2,8}-\d{2}$")
    category: str
    severity: Literal["blocking", "major", "minor"]
    check_method: Literal["tier0", "jev", "claim"]
    statement: str = Field(min_length=10, max_length=400)
    citation: PackCite
    also_cites: list[PackCite] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    examples_violating: list[str] = Field(min_length=1)
    examples_ok: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _checkable(self) -> "PackRule":
        if self.check_method == "tier0":
            pats = self.params.get("patterns")
            if not pats:
                raise ValueError(f"{self.code}: a tier0 pack rule needs params.patterns")
            for p in pats:
                re.compile(p)
        if self.check_method == "claim" and not self.params.get("superlative_patterns"):
            raise ValueError(f"{self.code}: a claim pack rule needs params.superlative_patterns")
        for reserved in ("citation", "also_cites", "pack", "pack_version", "linked_rules"):
            if reserved in self.params:
                raise ValueError(f"{self.code}: params.{reserved} is set by the pack loader")
        return self


class Pack(BaseModel):
    id: str
    version: int = Field(ge=1)
    name: str
    description: str
    retrieved: date
    sources: list[PackSource] = Field(min_length=1)
    rules: list[PackRule] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _consistent(self) -> "Pack":
        if not _ID.match(self.id):
            raise ValueError("pack id must be a lowercase slug")
        ids = [s.id for s in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate source id")
        codes = [r.code for r in self.rules]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate rule code")
        for r in self.rules:
            for c in [r.citation, *r.also_cites]:
                if c.source not in ids:
                    raise ValueError(f"{r.code}: unknown source {c.source}")
        return self

    def source(self, sid: str) -> PackSource:
        return next(s for s in self.sources if s.id == sid)


class PackNotFound(KeyError):
    pass


def load_pack_file(path: Path) -> Pack:
    return Pack.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def all_packs() -> tuple[Pack, ...]:
    return tuple(load_pack_file(p) for p in sorted(PACKS_DIR.glob("*.json")))


def get_pack(pack_id: str) -> Pack:
    for p in all_packs():
        if p.id == pack_id:
            return p
    raise PackNotFound(pack_id)


# ------------------------------------------------------------------ citation
def citation_dict(pack: Pack, r: PackRule) -> dict[str, Any]:
    src = pack.source(r.citation.source)
    return {
        "source_id": src.id, "document": src.title, "short": src.short, "clause": r.citation.clause,
        "quote": r.citation.quote, "url": src.url, "retrieved": src.retrieved.isoformat(), "status": src.status,
        "also": [{"document": pack.source(a.source).title, "clause": a.clause, "url": pack.source(a.source).url}
                 for a in r.also_cites],
    }


def source_quote(pack: Pack, r: PackRule) -> str:
    """What the finding card shows as the rule's source, e.g.
    Guidelines for Prevention and Regulation of Dark Patterns, 2023, Annexure 1 (1): False Urgency: “falsely stating …”"""
    src = pack.source(r.citation.source)
    q = f": “{r.citation.quote}”" if r.citation.quote else ""
    return f"{src.title}, {r.citation.clause}{q}"


# ------------------------------------------------------------------ links to the brand's own rules
def _spec(r: Rule) -> RuleSpec:
    return RuleSpec(code=r.code, category=r.category, severity=r.severity, statement=r.statement,
                    check_method=r.check_method, params=dict(r.params or {}), enabled=r.enabled)


def _pack_spec(r: PackRule) -> RuleSpec:
    return RuleSpec(code=r.code, category=r.category, severity=r.severity, statement=r.statement,
                    check_method=r.check_method, params=dict(r.params))


def _overlap(a: tuple[Optional[int], Optional[int]], b: tuple[Optional[int], Optional[int]]) -> bool:
    if None in a or None in b:
        return False
    return a[0] < b[1] and b[0] < a[1]  # type: ignore[operator]


def _sup_spans(params: dict[str, Any], text: str) -> list[tuple[int, int]]:
    out = []
    for p in params.get("superlative_patterns", []):
        try:
            out += [(m.start(), m.end()) for m in re.finditer(str(p), text, re.IGNORECASE) if m.end() > m.start()]
        except re.error:
            continue
    return out


def detect_links(r: PackRule, brand_rules: list[Rule]) -> list[str]:
    """Brand rules (tier0/claim, not from a pack) that flag the same words as `r` on r's violating examples."""
    linked: list[str] = []
    own = _pack_spec(r)
    ctx = CheckContext(channel="instagram", rules=[])
    for br in brand_rules:
        if br.pack_id is not None or br.check_method not in ("tier0", "claim"):
            continue
        spec = _spec(br)
        for ex in r.examples_violating:
            if r.check_method == "tier0":
                mine = [(h.span_start, h.span_end) for h in check_rule_on_text(own, ex, ctx, PostState())]
                theirs = [(h.span_start, h.span_end) for h in check_rule_on_text(spec, ex, ctx, PostState())
                          if h.kind not in ("exclaim", "caps")]
            elif r.check_method == "claim":
                mine, theirs = _sup_spans(r.params, ex), _sup_spans(spec.params, ex)
            else:
                break
            if any(_overlap(a, b) for a in mine for b in theirs):
                linked.append(br.code)
                break
    return linked


# ------------------------------------------------------------------ apply / remove / status
def _fields(pack: Pack, r: PackRule, linked: list[str]) -> dict[str, Any]:
    params = {**r.params, "pack": pack.id, "pack_version": pack.version, "citation": citation_dict(pack, r),
              "linked_rules": linked}
    return {"category": r.category, "severity": r.severity, "statement": r.statement,
            "source_quote": source_quote(pack, r), "page": None, "check_method": r.check_method, "params": params,
            "examples_violating": list(r.examples_violating), "examples_ok": list(r.examples_ok)}


def apply_pack(session: Session, brand_id: int, pack: Pack) -> dict[str, Any]:
    """Add or update the pack's rules for one brand (no commit). Never duplicates: matched by code."""
    rows = session.exec(select(Rule).where(Rule.brand_id == brand_id)).all()
    by_code = {r.code: r for r in rows}
    brand_rules = [r for r in rows if r.pack_id is None]
    added = updated = unchanged = 0
    skipped: list[str] = []
    links: dict[str, list[str]] = {}
    for pr in pack.rules:
        linked = detect_links(pr, brand_rules)
        if linked:
            links[pr.code] = linked
        fields = _fields(pack, pr, linked)
        row = by_code.get(pr.code)
        if row is not None and row.pack_id != pack.id:
            skipped.append(pr.code)  # the brand already uses this code for a rule of its own
            continue
        if row is None:
            session.add(Rule(brand_id=brand_id, code=pr.code, pack_id=pack.id, enabled=True, **fields))
            added += 1
            continue
        changes = {k: v for k, v in fields.items() if getattr(row, k) != v}
        if not changes:
            unchanged += 1
            continue
        for k, v in changes.items():
            setattr(row, k, v)  # JSON columns are reassigned, never mutated in place
        row.version += 1
        row.updated_at = utcnow()
        session.add(row)
        updated += 1
    return {"pack_id": pack.id, "added": added, "updated": updated, "unchanged": unchanged,
            "skipped": skipped, "linked": links}


def remove_pack(session: Session, brand_id: int, pack_id: str) -> int:
    rows = session.exec(select(Rule).where(Rule.brand_id == brand_id, Rule.pack_id == pack_id)).all()
    for r in rows:
        session.delete(r)
    return len(rows)


def pack_rows(session: Session, brand_id: int, pack_id: str) -> list[Rule]:
    return list(session.exec(select(Rule).where(Rule.brand_id == brand_id, Rule.pack_id == pack_id)).all())


def regulations_for(session: Session, brand_id: Optional[int], rule_code: str) -> list[Rule]:
    """Pack rules linked to a brand rule (see the module docstring)."""
    q = select(Rule).where(Rule.pack_id.is_not(None))  # type: ignore[union-attr]
    if brand_id is not None:
        q = q.where(Rule.brand_id == brand_id)
    return [r for r in session.exec(q.order_by(Rule.code)).all()
            if rule_code in ((r.params or {}).get("linked_rules") or [])]
