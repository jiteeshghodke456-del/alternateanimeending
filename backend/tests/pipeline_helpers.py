"""Shared helpers for pipeline tests: seed rules/terms/facts as plain specs (no DB)."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from app.pipeline.context import CheckContext, FactSpec, RuleSpec, TermSpec, UnitIn
from app.pipeline.units import split_text

SEED = Path(__file__).resolve().parents[1] / "app" / "seed"
TODAY = date(2026, 11, 7)


def seed_rules() -> list[RuleSpec]:
    keys = ("code", "category", "severity", "statement", "check_method", "params", "source_quote", "page",
            "examples_violating", "examples_ok")
    return [RuleSpec(**{k: r.get(k) for k in keys if k in r}) for r in json.loads((SEED / "rules.json").read_text())]


def seed_terms() -> list[TermSpec]:
    return [TermSpec(**t) for t in json.loads((SEED / "terms.json").read_text())]


def seed_facts() -> list[FactSpec]:
    rows = list(csv.DictReader((SEED / "facts.csv").open()))
    return [FactSpec(code=r["code"], statement=r["statement"], value=r["value"], source=r["source"],
                     valid_until=date.fromisoformat(r["valid_until"]) if r["valid_until"] else None) for r in rows]


def seed_ctx(text: str = "", channel: str = "instagram", **kw) -> CheckContext:  # noqa: ANN003
    brand = json.loads((SEED / "brand.json").read_text())
    palette = [(p["name"], p["hex"]) for p in json.loads((SEED / "palette.json").read_text())]
    return CheckContext(channel=channel, rules=seed_rules(), terms=seed_terms(), facts=seed_facts(), palette=palette,
                        tone_labels=brand["tone_labels"], channel_targets=brand["channel_targets"], post_text=text,
                        today=TODAY, **kw)


def text_units(text: str) -> list[UnitIn]:
    return [UnitIn(id=i + 1, kind="text", idx=u.idx, text=u.text, char_start=u.char_start) for i, u in enumerate(split_text(text))]


DEMO_TEXT = next(i for i in json.loads((SEED / "content" / "items.json").read_text()) if i.get("key") == "demo_flawed_post")["text"]
